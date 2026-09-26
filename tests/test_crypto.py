import pytest
from cryptography.hazmat.primitives import serialization

from actors import crypto


def test_encrypt_decrypt_roundtrip():
    token = crypto.encrypt(b"secret")
    assert token != b"secret"
    assert crypto.decrypt(token) == b"secret"


def test_decrypt_with_wrong_key_raises(settings):
    token = crypto.encrypt(b"secret")
    settings.FIELD_ENCRYPTION_KEY = (
        "ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2ZGV2"
    )
    with pytest.raises(crypto.KeyEncryptionError):
        crypto.decrypt(token)


def test_missing_key_raises(settings):
    settings.FIELD_ENCRYPTION_KEY = ""
    with pytest.raises(crypto.KeyEncryptionError):
        crypto.encrypt(b"x")


def test_invalid_key_raises(settings):
    settings.FIELD_ENCRYPTION_KEY = "not-a-valid-fernet-key"
    with pytest.raises(crypto.KeyEncryptionError):
        crypto.encrypt(b"x")


def test_generate_rsa_keypair_is_rsa_2048():
    private_pem, public_pem = crypto.generate_rsa_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    assert key.key_size == 2048
    assert public_pem.startswith(b"-----BEGIN PUBLIC KEY-----")


def test_generate_ed25519_keypair_sizes():
    private_bytes, public_bytes = crypto.generate_ed25519_keypair()
    assert len(private_bytes) == 32
    assert len(public_bytes) == 32
