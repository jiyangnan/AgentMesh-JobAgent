from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import ssl
import threading

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
import pytest

from jobagent.infra import cloud_client
from jobagent.infra import tls_support


@pytest.fixture
def tls_server(tmp_path):
    """Real TLS handshakes with ephemeral certificates; no external services."""
    now = datetime.now(timezone.utc)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Test root')])
    root = (x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
            .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False)
            .sign(root_key, hashes.SHA256()))
    ca_path = tmp_path / 'root.pem'
    ca_path.write_bytes(root.public_bytes(serialization.Encoding.PEM))

    @contextmanager
    def serve(*, expired=False, wrong_host=False):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(root_name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=2))
                .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([
                    x509.DNSName('wrong.example') if wrong_host else x509.IPAddress(ipaddress.ip_address('127.0.0.1'))
                ]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), critical=False)
                .sign(root_key, hashes.SHA256()))
        cert_path, key_path = tmp_path / 'server.pem', tmp_path / 'server.key'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'https://127.0.0.1:{server.server_port}', ca_path, seen
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    return serve


def empty_default_store(monkeypatch):
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    monkeypatch.delenv('SSL_CERT_DIR', raising=False)
    # Model a Python runtime whose default trust store has no roots.
    monkeypatch.setattr(ssl, 'create_default_context', lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    monkeypatch.setattr(cloud_client.urllib.request, '_opener', None)


def test_cloud_request_automatically_loads_bundled_roots_without_replay(monkeypatch, tls_server):
    with tls_server() as (url, ca, seen):
        empty_default_store(monkeypatch)
        monkeypatch.setattr(certifi, 'where', lambda: str(ca))
        monkeypatch.setattr(cloud_client, 'api_base_url', lambda: url)
        assert cloud_client.health() == {'status': 'ok'}
        assert seen == [('/v1/health', None)]


@pytest.mark.parametrize('kwargs,reason', [({'expired': True}, 'certificate_expired'), ({'wrong_host': True}, 'hostname_mismatch')])
def test_unacceptable_certificate_stays_blocked_with_actionable_redacted_reason(monkeypatch, tls_server, kwargs, reason):
    with tls_server(**kwargs) as (url, ca, seen):
        empty_default_store(monkeypatch)
        monkeypatch.setattr(certifi, 'where', lambda: str(ca))
        monkeypatch.setattr(cloud_client, 'api_base_url', lambda: url)
        with pytest.raises(cloud_client.CloudError) as exc:
            cloud_client.health()
        assert exc.value.code == 'tls_certificate_verification_failed'
        assert exc.value.retryable is False
        assert exc.value.attempts == 1
        assert exc.value.details['tls_diagnostic']['reason'] == reason
        assert isinstance(exc.value.details['tls_diagnostic']['verify_code'], int)
        assert seen == []


@pytest.mark.parametrize('variable,value', [('SSL_CERT_FILE', ''), ('SSL_CERT_DIR', '')])
def test_explicit_trust_settings_are_not_silently_augmented(monkeypatch, variable, value):
    empty_default_store(monkeypatch)
    monkeypatch.setenv(variable, value)
    monkeypatch.setattr(certifi, 'where', lambda: pytest.fail('custom trust must remain authoritative'))
    context = tls_support.verified_context()
    assert context.get_ca_certs() == []
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_invalid_custom_bundle_is_reported_without_path_leak(monkeypatch, tmp_path):
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'private-user-missing.pem'))
    with pytest.raises(tls_support.TLSConfigurationError) as exc:
        tls_support.verified_context()
    details = tls_support.failure_details(exc.value)
    assert details['tls_diagnostic']['reason'] == 'custom_ca_configuration_invalid'
    assert 'private-user' not in json.dumps(details)


def test_read_only_transport_probe_uses_no_credentials_and_preserves_state(monkeypatch, tls_server, tmp_path):
    with tls_server() as (url, ca, seen):
        empty_default_store(monkeypatch)
        monkeypatch.setattr(certifi, 'where', lambda: str(ca))
        monkeypatch.setenv('JOBAGENT_API_BASE', url)
        monkeypatch.setenv('JOBAGENT_CORE_API_BASE', url)
        monkeypatch.setenv('JOBAGENT_API_KEY', 'secret-must-not-be-sent')
        (tmp_path / 'work.sqlite3').write_bytes(b'preserved work and nonce')
        before = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
        probe = getattr(tls_support, 'transport_preflight', None)
        assert callable(probe), 'installation needs a bounded read-only transport preflight'
        result = probe()
        assert result['ok'] is True and result['account_verified'] is False
        assert len(seen) == 2 and all(auth is None for _, auth in seen)
        assert before == {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
        assert 'secret-must-not-be-sent' not in json.dumps(result)


def test_doctor_tls_bypasses_account_update_and_migrations(monkeypatch, capsys):
    from jobagent import cli
    import sys
    def forbidden(*a, **kw):
        pytest.fail('TLS diagnosis must not access or change business state')
    for name in ('_maybe_update', '_prepare_client_upgrade', '_verify_state_owner_for_command', '_dispatch'):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(tls_support, 'transport_preflight', lambda: {'ok': True, 'event': 'tls_preflight'}, raising=False)
    monkeypatch.setattr(sys, 'argv', ['jobagent', 'doctor', 'tls'])
    cli.main()
    assert json.loads(capsys.readouterr().out)['event'] == 'tls_preflight'


@pytest.mark.parametrize('isolated', [True, False])
def test_dependency_repair_is_bounded_and_never_targets_global_python(monkeypatch, isolated):
    import sys
    def missing(service, base):
        return {'service': service, 'ok': False,
                'tls_diagnostic': {'reason': 'bundled_ca_unavailable'}}
    monkeypatch.setattr(tls_support, '_probe', missing)
    monkeypatch.setattr(sys, 'prefix', '/isolated' if isolated else sys.base_prefix)
    result = tls_support.transport_preflight()
    assert result['ok'] is False
    if isolated:
        repair = result['dependency_repair']
        assert repair['command'][:3] == [sys.executable, '-m', 'pip']
        assert repair['command'][-1] == 'certifi>=2025.11.12'
        assert repair['max_attempts'] == 1
        assert repair['scope'] == 'current_virtual_environment'
    else:
        assert 'dependency_repair' not in result
        assert 'global Python' in result['agent_instructions']
