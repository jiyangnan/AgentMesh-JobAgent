from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import ssl
import urllib.error
import urllib.request

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID
import pytest

from jobagent.infra import cloud_client, release_update, tls_support
from jobagent.infra.protocol import ProtocolError, canonical_json_bytes
from tests.test_tls_support import empty_default_store, tls_server  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_trust_and_local_proxy(monkeypatch):
    for name in ('SSL_CERT_FILE', 'SSL_CERT_DIR'):
        monkeypatch.delenv(name, raising=False)
    # Real handshakes in this file are loopback-only and must not use host proxies.
    monkeypatch.setenv('NO_PROXY', '*')
    monkeypatch.setenv('no_proxy', '*')


def write_other_root(path: Path) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Independent test root')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=1))
                   .not_valid_after(now + timedelta(days=2))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                   .sign(key, hashes.SHA256()))
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return certificate.public_bytes(serialization.Encoding.DER)


def test_existing_system_root_is_preserved_while_bundle_is_added(monkeypatch, tls_server, tmp_path):
    with tls_server() as (url, system_ca, seen):
        system_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        system_context.load_verify_locations(cafile=str(system_ca))
        system_der = x509.load_pem_x509_certificate(system_ca.read_bytes()).public_bytes(serialization.Encoding.DER)
        bundle = tmp_path / 'additional-public-root.pem'
        bundled_der = write_other_root(bundle)
        assert bundled_der != system_der
        monkeypatch.setattr(ssl, 'create_default_context', lambda: system_context)
        monkeypatch.setattr(certifi, 'where', lambda: str(bundle))
        monkeypatch.setattr(cloud_client, 'api_base_url', lambda: url)

        assert cloud_client.health() == {'status': 'ok'}
        assert seen == [('/v1/health', None)]
        assert {system_der, bundled_der} <= set(system_context.get_ca_certs(binary_form=True))
        assert system_context.check_hostname is True
        assert system_context.verify_mode == ssl.CERT_REQUIRED


def test_unknown_ca_is_blocked_after_one_attempt_without_replay(monkeypatch, tls_server, tmp_path):
    with tls_server() as (url, _untrusted_ca, seen):
        empty_default_store(monkeypatch)
        bundle = tmp_path / 'unrelated-root.pem'
        write_other_root(bundle)
        monkeypatch.setattr(certifi, 'where', lambda: str(bundle))
        monkeypatch.setattr(cloud_client, 'api_base_url', lambda: url)
        monkeypatch.setattr(cloud_client.time, 'sleep', lambda _delay: pytest.fail('unknown CA must not retry'))
        real_urlopen = urllib.request.urlopen
        attempts = []

        def counted_urlopen(request, *, timeout, context):
            attempts.append(request.full_url)
            return real_urlopen(request, timeout=timeout, context=context)

        monkeypatch.setattr(urllib.request, 'urlopen', counted_urlopen)
        with pytest.raises(cloud_client.CloudError) as exc:
            cloud_client.health()

        assert len(attempts) == 1
        assert seen == []
        assert exc.value.code == 'tls_certificate_verification_failed'
        assert exc.value.retryable is False
        assert exc.value.attempts == 1
        assert exc.value.details['tls_diagnostic']['reason'] == 'untrusted_issuer'
        assert exc.value.details['next_suggested'] == 'jobagent doctor tls'


def test_explicit_valid_ca_directory_is_used_without_bundle(monkeypatch, tls_server, tmp_path):
    with tls_server() as (url, ca_path, seen):
        directory = tmp_path / 'explicit-trust-directory'
        directory.mkdir()
        # OpenSSL's subject-name hash for the shared fixture's fixed CN. The
        # certificate/key remain ephemeral; this avoids a CLI openssl dependency.
        subject = x509.load_pem_x509_certificate(ca_path.read_bytes()).subject
        assert subject == x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Test root')])
        subject_hash = '2faabc7f'
        (directory / (subject_hash + '.0')).write_bytes(ca_path.read_bytes())
        monkeypatch.setenv('SSL_CERT_DIR', str(directory))
        monkeypatch.setattr(certifi, 'where', lambda: pytest.fail('explicit CA directory must not be augmented'))
        monkeypatch.setattr(cloud_client, 'api_base_url', lambda: url)

        assert cloud_client.health() == {'status': 'ok'}
        assert seen == [('/v1/health', None)]
        assert tls_support.trust_source() == 'explicit_environment'


@pytest.mark.parametrize('tampered', [False, True])
def test_release_fetch_uses_verified_context_and_checks_manifest_signature(monkeypatch, tmp_path, tampered):
    empty_default_store(monkeypatch)
    bundle = tmp_path / 'bundled-root.pem'
    root_der = write_other_root(bundle)
    monkeypatch.setattr(certifi, 'where', lambda: str(bundle))
    signing_key = ed25519.Ed25519PrivateKey.generate()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
    public_key = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(release_update, 'RELEASE_SIGNING_PUBLIC_KEY', encode(public_key))
    cache = tmp_path / 'isolated-release-cache.json'
    monkeypatch.setattr(release_update, 'release_cache_path', lambda: cache)
    manifest = {
        'product': 'jobagent', 'channel': 'stable', 'protocol_version': cloud_client.PROTOCOL_VERSION,
        'latest_client_version': '0.6.15', 'minimum_supported_version': '0.6.0',
        'git_commit': '1' * 40, 'artifact_sha256': '2' * 64, 'signature_algorithm': 'Ed25519',
    }
    manifest['signature'] = encode(signing_key.sign(canonical_json_bytes(manifest)))
    if tampered:
        manifest['latest_client_version'] = '9.9.9'
    calls = []

    def urlopen(request, *, timeout, context):
        assert timeout == 10
        assert isinstance(context, ssl.SSLContext)
        assert context.check_hostname is True
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert root_der in context.get_ca_certs(binary_form=True)
        assert request.get_header('Authorization') is None
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(manifest).encode())

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    if tampered:
        with pytest.raises(ProtocolError, match='signature verification failed'):
            release_update.fetch_release_manifest(force=True)
        assert not cache.exists()
    else:
        assert release_update.fetch_release_manifest(force=True) == manifest
        assert json.loads(cache.read_text())['manifest'] == manifest
    assert len(calls) == 1


def test_cached_global_opener_does_not_override_explicit_context(monkeypatch, tls_server):
    with tls_server() as (url, ca, seen):
        empty_default_store(monkeypatch)
        monkeypatch.setattr(certifi, 'where', lambda: str(ca))
        monkeypatch.setattr(cloud_client, 'api_base_url', lambda: url)

        class CachedOpener:
            def open(self, *args, **kwargs):
                pytest.fail('a stale global opener must not handle the verified request')

        old_opener = CachedOpener()
        monkeypatch.setattr(urllib.request, '_opener', old_opener)
        assert cloud_client.health() == {'status': 'ok'}
        assert seen == [('/v1/health', None)]
        assert urllib.request._opener is old_opener


@pytest.mark.parametrize('wrapped', [False, True])
def test_unknown_verification_message_is_never_exposed(monkeypatch, tmp_path, wrapped):
    empty_default_store(monkeypatch)
    bundle = tmp_path / 'bundle.pem'
    write_other_root(bundle)
    monkeypatch.setattr(certifi, 'where', lambda: str(bundle))
    secret = 'private-user:/home/private/ca.pem@proxy.internal?token=secret-value'
    failure = ssl.SSLCertVerificationError(1, 'CERTIFICATE_VERIFY_FAILED ' + secret)
    failure.verify_code = 987654
    failure.verify_message = secret
    calls = []

    def urlopen(request, *, timeout, context):
        calls.append(request.full_url)
        raise urllib.error.URLError(failure) if wrapped else failure

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    with pytest.raises(cloud_client.CloudError) as exc:
        cloud_client.health()
    assert len(calls) == 1
    assert exc.value.code == 'tls_certificate_verification_failed'
    details = exc.value.details
    assert details['tls_diagnostic']['reason'] == 'certificate_verification_failed'
    assert details['tls_diagnostic']['verify_code'] == 987654
    assert secret not in json.dumps(details) + str(exc.value)
    assert 'private-user' not in json.dumps(details) + str(exc.value)


@pytest.mark.parametrize('variable,kind', [
    ('SSL_CERT_FILE', 'missing'), ('SSL_CERT_FILE', 'directory'), ('SSL_CERT_FILE', 'malformed'),
    ('SSL_CERT_DIR', 'missing'), ('SSL_CERT_DIR', 'file'),
])
def test_invalid_explicit_trust_has_typed_redacted_failure(monkeypatch, tmp_path, variable, kind):
    path = tmp_path / 'private-user-ca-location'
    if kind == 'directory':
        path.mkdir()
    elif kind in ('file', 'malformed'):
        path.write_text('private-user invalid certificate material')
    (tmp_path / 'business-state-sentinel').write_bytes(b'original work, request and nonce')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    monkeypatch.setenv(variable, str(path))
    monkeypatch.setattr(certifi, 'where', lambda: pytest.fail('invalid explicit trust must not trigger a fallback'))
    with pytest.raises(tls_support.TLSConfigurationError) as exc:
        tls_support.verified_context()
    details = tls_support.failure_details(exc.value)
    assert details['tls_diagnostic']['reason'] == 'custom_ca_configuration_invalid'
    assert details['tls_diagnostic']['trust_source'] == 'explicit_environment'
    assert 'private-user' not in str(exc.value) + json.dumps(details)
    assert str(path) not in str(exc.value) + json.dumps(details)
    assert details['next_suggested'] == 'jobagent doctor tls'
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert after == before


@pytest.mark.parametrize('failure_type,expected_error,tls_verified', [
    ('http500', 'cloud_http_error', True),
    ('timeout', 'network_connection_failed', False),
])
def test_preflight_network_or_http_error_is_not_certificate_failure(monkeypatch, tmp_path, failure_type, expected_error, tls_verified):
    empty_default_store(monkeypatch)
    bundle = tmp_path / 'bundle.pem'
    write_other_root(bundle)
    monkeypatch.setattr(certifi, 'where', lambda: str(bundle))
    monkeypatch.setenv('JOBAGENT_API_BASE', 'https://cloud.example.invalid')
    monkeypatch.setenv('JOBAGENT_CORE_API_BASE', 'https://core.example.invalid')
    monkeypatch.setenv('JOBAGENT_API_KEY', 'secret-never-send')
    calls = []

    def urlopen(request, *, timeout, context):
        assert timeout <= 5
        assert request.get_method() == 'GET'
        assert request.get_header('Authorization') is None
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        calls.append(request.full_url)
        if failure_type == 'http500':
            raise urllib.error.HTTPError(request.full_url, 500, 'private-secret-response', {}, io.BytesIO(b'private-secret-body'))
        raise urllib.error.URLError(TimeoutError('private-secret-network-error'))

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    result = tls_support.transport_preflight()
    assert len(calls) == 2
    assert result['ok'] is False and result['account_verified'] is False
    assert result['request_preserved'] is True
    assert result['resume_original_command'] is False
    assert len(result['checks']) == 2
    for check in result['checks']:
        assert check['error'] == expected_error
        assert check['tls_verified'] is tls_verified
        assert 'tls_diagnostic' not in check
    assert 'private-secret' not in json.dumps(result)
    assert 'secret-never-send' not in json.dumps(result)
