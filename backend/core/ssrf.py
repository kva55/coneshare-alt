"""Outbound HTTP safety (SSRF protection) for admin-configured destinations such as webhooks.

Two layers:

* ``validate_outbound_url`` checks a URL when it is saved or before it is used:
  scheme, credentials, port, and that every address the hostname resolves to is allowed.
* ``pinned_request`` performs the request. It resolves the hostname once, validates
  every resolved address, and then connects to that exact IP address while sending the
  original hostname in the Host header and using it for TLS SNI and certificate
  verification. Because the socket is opened to the validated IP, a second DNS answer
  (DNS rebinding) cannot redirect the request to an internal address. Environment
  proxies are ignored and redirects are never followed.

Address policy (nothing is hard-coded as an exception):

* always blocked: loopback, link-local (incl. 169.254.169.254 metadata), unspecified,
  multicast, reserved, and the AWS IPv6 metadata range;
* blocked by default: private / shared address space (RFC 1918, CGNAT 100.64/10,
  IPv6 ULA fc00::/7). Specific networks can be allowed with the
  ``OUTBOUND_ALLOWED_NETWORKS`` setting (comma-separated CIDRs in the environment).
  The allow-list never overrides the "always blocked" categories.
"""
import ipaddress
import json as jsonlib
import logging
import socket
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urlsplit

import certifi
import urllib3
from django.conf import settings
from rest_framework import serializers

logger = logging.getLogger(__name__)


class DestinationResolutionError(serializers.ValidationError):
    """Raised when destination hostname DNS resolution fails (may be transient)."""
    pass


class OutboundRequestError(Exception):
    """Raised when a pinned outbound request fails at the transport level."""
    pass


AWS_IMDSV6_NET = ipaddress.ip_network('fd00:ec2::/64')
CGNAT_NET = ipaddress.ip_network('100.64.0.0/10')

FORBIDDEN_WEBHOOK_HEADERS = {
    'host',
    'content-length',
    'transfer-encoding',
    'connection',
    'upgrade',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailers',
    'x-internal-api-key',
    'x-forwarded-for',
    'x-real-ip',
    'x-original-url',
    'x-rewrite-url',
}

MAX_RESPONSE_BYTES = 64 * 1024


def _normalize_ip(ip_str: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    clean_ip = ip_str.split('%', 1)[0].strip('[]')
    ip = ipaddress.ip_address(clean_ip)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def parse_allowed_networks(raw: Optional[str]) -> list:
    """Parse a comma-separated CIDR list; raises ValueError on an invalid entry."""
    networks = []
    for entry in (raw or '').split(','):
        entry = entry.strip()
        if entry:
            networks.append(ipaddress.ip_network(entry, strict=False))
    return networks


def _allowed_networks() -> list:
    return list(getattr(settings, 'OUTBOUND_ALLOWED_NETWORKS', []) or [])


def _always_blocked(ip) -> bool:
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or ip in AWS_IMDSV6_NET
    )


def _private(ip) -> bool:
    return ip.is_private or (isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT_NET)


def check_address_allowed(ip, allowed_networks: Optional[Iterable] = None) -> None:
    """Raise ValidationError if ``ip`` may not be contacted."""
    if _always_blocked(ip):
        raise serializers.ValidationError("Loopback, link-local and cloud metadata addresses are not permitted.")
    if _private(ip):
        nets = list(allowed_networks) if allowed_networks is not None else _allowed_networks()
        if not any(ip.version == net.version and ip in net for net in nets):
            raise serializers.ValidationError(
                "Private network addresses are not permitted. "
                "An administrator can allow specific networks with OUTBOUND_ALLOWED_NETWORKS."
            )


@dataclass
class ResolvedDestination:
    scheme: str
    hostname: str
    port: int
    path: str
    addresses: list = field(default_factory=list)  # validated IP strings, in resolver order

    @property
    def default_port(self) -> bool:
        return self.port == (443 if self.scheme == 'https' else 80)


def resolve_destination(url: str) -> ResolvedDestination:
    """Validate ``url`` and resolve it to a list of allowed IP addresses."""
    if not url:
        raise serializers.ValidationError("URL must not be empty.")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ('http', 'https'):
        raise serializers.ValidationError("Endpoint URL must use HTTP or HTTPS protocol.")
    if parts.username or parts.password:
        raise serializers.ValidationError("Credentials in URLs are not permitted.")

    hostname = (parts.hostname or '').strip().rstrip('.').lower()
    if not hostname:
        raise serializers.ValidationError("URL must contain a valid hostname.")
    if hostname == 'localhost' or hostname.endswith('.localhost'):
        raise serializers.ValidationError("Loopback addresses are not permitted.")

    try:
        port = parts.port or (443 if scheme == 'https' else 80)
    except ValueError:
        raise serializers.ValidationError("URL contains an invalid port.") from None

    try:
        addr_info = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DestinationResolutionError(f"Cannot resolve destination hostname: {exc}") from exc
    if not addr_info:
        raise serializers.ValidationError(f"No address records found for hostname '{hostname}'.")

    allowed = _allowed_networks()
    addresses = []
    for _, _, _, _, sockaddr in addr_info:
        try:
            ip_obj = _normalize_ip(sockaddr[0])
        except ValueError:
            raise serializers.ValidationError("Invalid destination IP address format.")
        try:
            check_address_allowed(ip_obj, allowed)
        except serializers.ValidationError:
            # Reject the whole hostname if ANY record is disallowed, so round-robin or
            # rebinding answers cannot mix a public and an internal address.
            logger.warning("Destination host '%s' resolved to blocked address '%s'", hostname, sockaddr[0])
            raise
        if str(ip_obj) not in addresses:
            addresses.append(str(ip_obj))

    path = parts.path or '/'
    if parts.query:
        path = f"{path}?{parts.query}"
    return ResolvedDestination(scheme=scheme, hostname=hostname, port=port, path=path, addresses=addresses)


def validate_outbound_url(url: str) -> str:
    """Validate an outbound URL (scheme, credentials, port, resolved addresses). Returns the URL."""
    if not url:
        return url
    resolve_destination(url)
    return url


@dataclass
class PinnedResponse:
    status_code: int
    text: str
    headers: dict


def pinned_request(method: str, url: str, *, json=None, body=None, headers=None, timeout: float = 10) -> PinnedResponse:
    """
    Perform an HTTP(S) request to ``url`` connecting only to an address validated by
    ``resolve_destination``. Redirects are not followed; environment proxies are not used.
    """
    dest = resolve_destination(url)
    ip = dest.addresses[0]

    request_headers = {str(k): str(v) for k, v in (headers or {}).items()
                       if str(k).strip().lower() not in FORBIDDEN_WEBHOOK_HEADERS}
    request_headers['Host'] = dest.hostname if dest.default_port else f"{dest.hostname}:{dest.port}"
    if json is not None and body is None:
        body = jsonlib.dumps(json).encode('utf-8')
    if body is not None:
        request_headers.setdefault('Content-Type', 'application/json')

    pool_timeout = urllib3.Timeout(connect=timeout, read=timeout)
    if dest.scheme == 'https':
        pool = urllib3.HTTPSConnectionPool(
            ip, port=dest.port, timeout=pool_timeout, retries=False, maxsize=1,
            cert_reqs='CERT_REQUIRED', ca_certs=certifi.where(),
            server_hostname=dest.hostname, assert_hostname=dest.hostname,
        )
    else:
        pool = urllib3.HTTPConnectionPool(ip, port=dest.port, timeout=pool_timeout, retries=False, maxsize=1)

    try:
        resp = pool.urlopen(
            method.upper(), dest.path, body=body, headers=request_headers,
            redirect=False, retries=False, preload_content=False,
        )
        try:
            data = resp.read(MAX_RESPONSE_BYTES, decode_content=True) or b''
        finally:
            resp.release_conn()
    except urllib3.exceptions.HTTPError as exc:
        raise OutboundRequestError(str(exc)) from exc
    finally:
        pool.close()

    return PinnedResponse(
        status_code=resp.status,
        text=data.decode('utf-8', errors='replace'),
        headers=dict(resp.headers),
    )


def validate_webhook_headers(headers: dict) -> dict:
    """
    Sanitizes and validates custom headers for webhook destinations.
    Rejects hop-by-hop and internal spoofing headers.
    """
    if not headers or not isinstance(headers, dict):
        return headers

    for key in headers:
        if str(key).strip().lower() in FORBIDDEN_WEBHOOK_HEADERS:
            raise serializers.ValidationError(
                f"Header '{key}' is restricted and cannot be customized."
            )

    return headers
