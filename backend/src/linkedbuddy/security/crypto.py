"""Fernet encryption for tokens stored in the DB (`*_enc` bytea columns)."""

from cryptography.fernet import Fernet, InvalidToken

from linkedbuddy.config import get_settings


class InvalidEncryptionKey(ValueError):
    """APP_ENCRYPTION_KEY is not a valid Fernet key."""


def _fernet(key: str | None) -> Fernet:
    raw = key if key is not None else get_settings().app_encryption_key.get_secret_value()
    try:
        return Fernet(raw.encode())
    except ValueError as exc:
        raise InvalidEncryptionKey(
            "Encryption key is not a valid Fernet key (32 url-safe base64-encoded bytes). "
            "Generate one with: python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        ) from exc


def encrypt(plain: str, *, key: str | None = None) -> bytes:
    return _fernet(key).encrypt(plain.encode())


def decrypt(token: bytes, *, key: str | None = None) -> str:
    try:
        return _fernet(key).decrypt(token).decode()
    except InvalidToken as exc:
        raise ValueError("Cannot decrypt token: wrong key or corrupted data") from exc
