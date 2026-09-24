"""Verified HTTPS with product-owned roots; never modify host trust settings."""
from __future__ import annotations

import http.client
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TLSConfigurationError(ssl.SSLError):
    def __init__(self, reason: str):
        self.reason_code = reason
        super().__init__('The HTTPS trust configuration could not be loaded.')


def trust_source() -> str:
    return ('explicit_environment' if any(k in os.environ for k in ('SSL_CERT_FILE', 'SSL_CERT_DIR'))
            else 'system_and_bundled_roots')


def verified_context() -> ssl.SSLContext:
    """Augment default trust, but never replace an explicitly configured store."""
    try:
        for name, check in (('SSL_CERT_FILE', os.path.isfile), ('SSL_CERT_DIR', os.path.isdir)):
            value = os.environ.get(name)
            if value and not check(value):
                raise TLSConfigurationError('custom_ca_configuration_invalid')
        context = ssl.create_default_context()
        if trust_source() == 'system_and_bundled_roots':
            import certifi
            context.load_verify_locations(cafile=certifi.where())
        else:
            cafile = os.environ.get('SSL_CERT_FILE') or None
            capath = os.environ.get('SSL_CERT_DIR') or None
            if cafile or capath:
                # OpenSSL's default loader can silently ignore a malformed file.
                context.load_verify_locations(cafile=cafile, capath=capath)
        context.set_alpn_protocols(['http/1.1'])
        return context
    except (OSError, ImportError) as exc:
        reason = ('custom_ca_configuration_invalid' if trust_source() == 'explicit_environment'
                  else 'bundled_ca_unavailable')
        raise TLSConfigurationError(reason) from exc


def failure_details(error: BaseException) -> dict[str, Any]:
    """Expose fixed reason codes, never raw certificate, proxy or path text."""
    if isinstance(error, TLSConfigurationError):
        reason, code = error.reason_code, None
    elif isinstance(error, ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in str(error).upper():
        code = getattr(error, 'verify_code', None)
        code = code if isinstance(code, int) else None
        reason = {9: 'certificate_not_yet_valid', 10: 'certificate_expired',
                  18: 'untrusted_issuer', 19: 'untrusted_issuer', 20: 'untrusted_issuer',
                  21: 'untrusted_issuer', 62: 'hostname_mismatch', 64: 'hostname_mismatch'}.get(
                      code, 'certificate_verification_failed')
    else:
        return {}
    return {
        'tls_diagnostic': {'reason': reason, 'verify_code': code, 'trust_source': trust_source(),
                           'certificate_verification_required': True},
        'next_suggested': 'jobagent doctor tls',
        'agent_instructions': (
            'Run jobagent doctor tls once using this installation. Bundled public roots are already '
            'loaded automatically unless SSL_CERT_FILE or SSL_CERT_DIR is explicitly configured. '
            'Preserve existing work and custom trust settings. Never disable certificate or hostname '
            'verification, change Keychain, reinitialize the account, or repeat a business action. '
            'If the check remains blocked, relay its specific reason and recovery guidance.'
        ),
    }


def _probe(service: str, base: str) -> dict[str, Any]:
    try:
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return {'service': service, 'ok': False, 'error': 'https_endpoint_required'}
        request = urllib.request.Request(base.rstrip('/') + '/v1/health', headers={'Accept': 'application/json'})
        # Public health endpoints can take several seconds on a healthy route.
        # Both probes run in parallel; retain a bounded wait without mistaking
        # ordinary network latency for an unusable HTTPS connection.
        with urllib.request.urlopen(request, timeout=15, context=verified_context()) as response:
            return {'service': service, 'ok': response.status == 200, 'http_status': response.status,
                    'tls_verified': urllib.parse.urlsplit(response.geturl()).scheme == 'https'}
    except urllib.error.HTTPError as exc:
        return {'service': service, 'ok': False, 'tls_verified': True,
                'error': 'cloud_http_error', 'http_status': exc.code}
    except (OSError, urllib.error.URLError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        detail = failure_details(reason)
        return {'service': service, 'ok': False, 'tls_verified': False,
                'error': ('tls_trust_configuration_failed' if isinstance(reason, TLSConfigurationError)
                          else 'tls_certificate_verification_failed' if detail
                          else 'network_connection_failed'),
                **({'tls_diagnostic': detail['tls_diagnostic']} if detail else {})}
    except ValueError:
        return {'service': service, 'ok': False, 'tls_verified': False,
                'error': 'https_endpoint_invalid'}
    except http.client.HTTPException:
        return {'service': service, 'ok': False, 'tls_verified': False,
                'error': 'http_protocol_error'}


def transport_preflight() -> dict[str, Any]:
    """Two bounded, unauthenticated GETs; no account, work or browser access."""
    from concurrent.futures import ThreadPoolExecutor
    from jobagent.infra.credentials import api_base_url

    targets = [('agentmesh_cloud', api_base_url()),
               ('agentmesh_core', os.environ.get('JOBAGENT_CORE_API_BASE', 'https://api.agentmesh360.com'))]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda item: _probe(*item), targets))
    ok = all(item['ok'] and item.get('tls_verified') for item in results)
    guidance = (
        'HTTPS checks passed. Resume the original command and preserve its work and authorization.' if ok else
        'HTTPS is not ready. Package installation and account verification are separate. '
        'Do not reinstall repeatedly or recreate work. Check the reported trust configuration; '
        'for expired/not-yet-valid certificates check system time and contact service support if time is correct; '
        'for hostname/issuer failures check the configured proxy or managed network with its administrator. '
        'Do not import an unverified certificate or disable TLS verification. Repeat this check only after a concrete repair.'
    )
    result: dict[str, Any] = {
        'ok': ok, 'event': 'tls_preflight', 'account_verified': False,
        'request_preserved': True, 'trust_source': trust_source(), 'checks': results,
        'resume_original_command': ok, 'agent_instructions': guidance,
        'user_prompt': ('HTTPS 连接检查通过，继续原操作。' if ok else
                        '客户端已安装，但 HTTPS 连接检查未通过。现有任务已保留，请按具体诊断处理证书或网络配置。'),
    }
    if ok:
        # The probe reads no business state and grants no browser permission.
        # Recheck the account/workflow through a safe CLI entry after repair.
        result['next_suggested'] = 'jobagent doctor env'
    if any(item.get('tls_diagnostic', {}).get('reason') == 'bundled_ca_unavailable' for item in results):
        import sys
        if sys.prefix != sys.base_prefix:
            result['dependency_repair'] = {
                'command': [sys.executable, '-m', 'pip', 'install', '--force-reinstall', 'certifi>=2025.11.12'],
                'max_attempts': 1, 'scope': 'current_virtual_environment',
                'next_suggested': 'jobagent doctor tls',
            }
            result['agent_instructions'] = (
                'Repair the missing product CA dependency once using dependency_repair.command, then run '
                'jobagent doctor tls in a fresh process. If it succeeds resume the original command. '
                'Do not use trusted-host, change global configuration, or loop if repair fails.'
            )
        else:
            result['agent_instructions'] = (
                'The product CA dependency is missing outside an isolated Python environment. '
                'Use the official installer to provision the product environment while preserving existing state; '
                'do not install into or change the global Python or disable TLS verification.'
            )
    return result
