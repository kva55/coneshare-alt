"""Single place the application sends email from.

Every email the app sends (access codes, invitations, notifications) goes through
``send_app_mail``. The transport comes from the admin UI (``automations.EmailSettings``) when
that is enabled, otherwise from the EMAIL_* environment settings.

UI-configured SMTP hosts are admin-supplied, so they get the same SSRF treatment as webhooks:
the host is resolved once, every address must be allowed (see core/ssrf.py), and the socket
connects to the validated address while TLS still verifies the original hostname.
"""
import imaplib
import logging
import smtplib
import socket
import ssl
import time

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
from django.core.mail.utils import DNS_NAME

from .ssrf import resolve_destination

logger = logging.getLogger(__name__)


class _Pinned:
    """Mixin for smtplib classes: connect to ``pinned_ip`` but keep ``host`` for TLS checks."""
    pinned_ip = None

    def _get_socket(self, host, port, timeout):
        return super()._get_socket(self.pinned_ip or host, port, timeout)


class _PinnedSMTP(_Pinned, smtplib.SMTP):
    pass


class _PinnedSMTPSSL(_Pinned, smtplib.SMTP_SSL):
    pass


class PinnedSMTPBackend(SMTPBackend):
    """Django SMTP backend that connects only to an address validated just before connecting."""

    def open(self):
        if self.connection:
            return False
        # Validation happens here, at connect time, and the validated address is the one used.
        resolved = resolve_destination(f"https://{self.host}:{self.port}")
        connection_class = _PinnedSMTPSSL if self.use_ssl else _PinnedSMTP
        params = {'local_hostname': DNS_NAME.get_fqdn()}
        if self.timeout is not None:
            params['timeout'] = self.timeout
        if self.use_ssl:
            params['context'] = self.ssl_context
        try:
            connection = connection_class.__new__(connection_class)
            connection.pinned_ip = resolved.addresses[0]
            connection.__init__(self.host, self.port, **params)
            self.connection = connection
            if not self.use_ssl and self.use_tls:
                self.connection.starttls(context=self.ssl_context)
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            return True
        except OSError:
            if not self.fail_silently:
                raise


class _PinnedIMAP(imaplib.IMAP4):
    """Plain IMAP that connects to ``pinned_ip`` (validated just before connecting)."""
    pinned_ip = None

    def _create_socket(self, timeout=None):
        return socket.create_connection((self.pinned_ip, self.port), timeout)


class _PinnedIMAPSSL(imaplib.IMAP4_SSL):
    """IMAP over TLS: connects to ``pinned_ip`` but verifies the certificate for the host name."""
    pinned_ip = None

    def _create_socket(self, timeout=None):
        sock = socket.create_connection((self.pinned_ip, self.port), timeout)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


def _connect_imap(config):
    """Open and log in to the configured inbox. Host is validated and pinned; TLS is verified."""
    resolved = resolve_destination(f"https://{config.imap_host}:{config.imap_port}")
    if config.imap_use_ssl:
        client = _PinnedIMAPSSL.__new__(_PinnedIMAPSSL)
        client.pinned_ip = resolved.addresses[0]
        client.__init__(config.imap_host, config.imap_port, ssl_context=ssl.create_default_context(), timeout=10)
    else:
        client = _PinnedIMAP.__new__(_PinnedIMAP)
        client.pinned_ip = resolved.addresses[0]
        client.__init__(config.imap_host, config.imap_port, timeout=10)
    if config.imap_username:
        client.login(config.imap_username, config.imap_password)
    return client


def check_inbox_for_token(config, token: str, wait_seconds: int = 20):
    """
    Wait for the message whose subject contains ``token`` to arrive in the configured inbox,
    then delete just that message. Returns (found, seconds_waited). Nothing else in the mailbox
    is read, listed or changed.
    """
    started = time.monotonic()
    client = _connect_imap(config)
    try:
        client.select('INBOX')
        while True:
            kind, data = client.search(None, 'SUBJECT', f'"{token}"')
            numbers = data[0].split() if kind == 'OK' and data and data[0] else []
            if numbers:
                for number in numbers:
                    client.store(number, '+FLAGS', '\\Deleted')
                client.expunge()
                return True, round(time.monotonic() - started, 1)
            if time.monotonic() - started >= wait_seconds:
                return False, round(time.monotonic() - started, 1)
            time.sleep(2)
            client.noop()
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _ui_connection():
    """(connection, from_email) from the admin-configured settings, or (None, None)."""
    from automations.models import EmailSettings  # local: core must not import apps at load time
    config = EmailSettings.load()
    if not config.pk or not config.enabled:
        return None, None
    connection = PinnedSMTPBackend(
        host=config.host, port=config.port, username=config.username or None,
        password=config.password or None, use_tls=config.use_tls, use_ssl=config.use_ssl,
        timeout=10, fail_silently=False,
    )
    return connection, config.from_email


def send_app_mail(subject: str, text: str, recipients, html: str | None = None) -> None:
    """Send an email; raises on transport failure so callers can decide how to react."""
    if isinstance(recipients, str):
        recipients = [recipients]
    connection, from_email = _ui_connection()
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=list(recipients),
        connection=connection,
    )
    if html:
        message.attach_alternative(html, 'text/html')
    message.send(fail_silently=False)
