import pytest
from cryptography.fernet import Fernet

from linkedbuddy.security.crypto import InvalidEncryptionKey, decrypt, encrypt


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


def test_roundtrip(key: str) -> None:
    token = encrypt("abc", key=key)
    assert token != b"abc"
    assert isinstance(token, bytes)
    assert decrypt(token, key=key) == "abc"


def test_roundtrip_uses_settings_key(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    from pydantic import SecretStr

    from linkedbuddy.security import crypto

    class _Fake:
        app_encryption_key = SecretStr(key)

    monkeypatch.setattr(crypto, "get_settings", lambda: _Fake())
    assert decrypt(encrypt("abc")) == "abc"


def test_wrong_key_cannot_decrypt(key: str) -> None:
    token = encrypt("abc", key=key)
    with pytest.raises(ValueError, match="decrypt"):
        decrypt(token, key=Fernet.generate_key().decode())


def test_invalid_key_raises_clear_error() -> None:
    with pytest.raises(InvalidEncryptionKey, match="Fernet"):
        encrypt("abc", key="not-a-fernet-key")
