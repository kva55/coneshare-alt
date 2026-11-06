import socket
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.exceptions import ValidationError

from core import ssrf


def _addrinfo(*ips, port=443):
    out = []
    for ip in ips:
        family = socket.AF_INET6 if ':' in ip else socket.AF_INET
        sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
        out.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr))
    return out


def _fake_pool(status=200, body=b'ok'):
    pool = MagicMock()
    resp = MagicMock()
    resp.status = status
    resp.headers = {'Content-Type': 'text/plain'}
    resp.read.return_value = body
    pool.urlopen.return_value = resp
    return pool


@patch('core.ssrf.socket.getaddrinfo')
def test_https_request_connects_to_the_validated_ip_with_original_host(mock_gai):
    mock_gai.return_value = _addrinfo('93.184.216.34')
    pool = _fake_pool()
    with patch('core.ssrf.urllib3.HTTPSConnectionPool', return_value=pool) as pool_cls:
        resp = ssrf.pinned_request('POST', 'https://hooks.example.com/path?a=1',
                                   json={'k': 'v'}, headers={'X-Sig': 'abc', 'Host': 'evil.internal'})

    args, kwargs = pool_cls.call_args
    assert args[0] == '93.184.216.34'                      # socket goes to the validated IP
    assert kwargs['server_hostname'] == 'hooks.example.com'  # SNI uses the hostname
    assert kwargs['assert_hostname'] == 'hooks.example.com'  # certificate checked for the hostname
    assert kwargs['cert_reqs'] == 'CERT_REQUIRED'

    _, call_kwargs = pool.urlopen.call_args
    assert pool.urlopen.call_args[0][:2] == ('POST', '/path?a=1')
    assert call_kwargs['headers']['Host'] == 'hooks.example.com'  # caller cannot override Host
    assert call_kwargs['headers']['X-Sig'] == 'abc'
    assert call_kwargs['redirect'] is False
    assert call_kwargs['body'] == b'{"k": "v"}'
    assert resp.status_code == 200 and resp.text == 'ok'


@patch('core.ssrf.socket.getaddrinfo')
def test_rebinding_to_internal_address_is_refused_before_connecting(mock_gai):
    # Validation earlier saw a public IP; at send time DNS now answers with metadata.
    mock_gai.return_value = _addrinfo('169.254.169.254', port=80)
    with patch('core.ssrf.urllib3.HTTPConnectionPool') as pool_cls:
        with pytest.raises(ValidationError):
            ssrf.pinned_request('POST', 'http://hooks.example.com/x', json={})
    pool_cls.assert_not_called()


@patch('core.ssrf.socket.getaddrinfo')
def test_mixed_public_and_private_answers_are_refused(mock_gai):
    mock_gai.return_value = _addrinfo('93.184.216.34', '10.0.0.7')
    with pytest.raises(ValidationError):
        ssrf.resolve_destination('https://hooks.example.com/x')


@pytest.mark.parametrize('ip', ['127.0.0.1', '::1', '0.0.0.0', '169.254.1.1', 'fe80::1',
                                '224.0.0.1', '240.0.0.1', '::ffff:127.0.0.1', 'fd00:ec2::254'])
def test_always_blocked_addresses(ip):
    with pytest.raises(ValidationError):
        ssrf.check_address_allowed(ssrf._normalize_ip(ip), allowed_networks=[])


@pytest.mark.parametrize('ip', ['10.1.2.3', '172.16.0.1', '192.168.0.1', '100.64.0.1', 'fd12::1'])
def test_private_addresses_blocked_unless_allow_listed(ip):
    addr = ssrf._normalize_ip(ip)
    with pytest.raises(ValidationError):
        ssrf.check_address_allowed(addr, allowed_networks=[])
    import ipaddress
    net = ipaddress.ip_network(f'{ip}/{32 if addr.version == 4 else 128}')
    ssrf.check_address_allowed(addr, allowed_networks=[net])  # no exception


def test_localhost_names_and_credentials_rejected():
    for url in ('http://localhost/x', 'http://api.localhost/x', 'https://user:pw@example.com/x',
                'ftp://example.com/x', 'http:///nohost'):
        with pytest.raises(ValidationError):
            ssrf.resolve_destination(url)


def test_parse_allowed_networks():
    nets = ssrf.parse_allowed_networks(' 10.0.0.0/8, 192.168.1.5 ,,')
    assert [str(n) for n in nets] == ['10.0.0.0/8', '192.168.1.5/32']
    with pytest.raises(ValueError):
        ssrf.parse_allowed_networks('not-a-network')
