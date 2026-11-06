"""Small secret helpers built on SECRET_KEY (no per-field encryption library needed).

* Webhook signing secrets are *derived*, never stored: ``derive_webhook_secret`` is a keyed hash
  of the endpoint id and a version counter, so a database dump contains no signing secrets and
  rotating is just bumping the counter.
* The SMTP password (the one secret the app must be able to read back) is encrypted with
  Fernet using a key derived from SECRET_KEY. Rotating SECRET_KEY makes the stored password
  unreadable; the admin re-enters it.
"""
import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def derive_webhook_secret(endpoint_id: str, version: int) -> str:
    message = f"webhook-secret:{endpoint_id}:{version}".encode()
    return 'whsec_' + hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    key = hashlib.sha256(b"email-settings:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode() if plain else ''


def decrypt_secret(token: str) -> str:
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ''


def sign_body(secret: str, body: bytes) -> str:
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
