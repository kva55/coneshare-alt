"""One-time codes: generation and keyed hashing. Only the hash is ever stored."""
import hashlib
import hmac
import secrets

from django.conf import settings

# No look-alike characters (0/O, 1/I/L) so a code can be read out or typed reliably.
ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
LENGTH = 8


def generate_code() -> str:
    return ''.join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def display_code(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    return ''.join(ch for ch in (code or '').upper() if ch.isalnum())


def hash_code(namespace: str, email: str, code: str) -> str:
    message = f"{namespace}:{email.strip().lower()}:{normalize_code(code)}"
    return hmac.new(settings.SECRET_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()


def codes_match(expected_hash: str, namespace: str, email: str, code: str) -> bool:
    return hmac.compare_digest(expected_hash, hash_code(namespace, email, code))
