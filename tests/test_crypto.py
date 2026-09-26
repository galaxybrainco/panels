import importlib
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization

from actors import crypto

SECOND_VALID_KEY = "khtNoDFXLyvfkh2vVB8GRQmFfz_Q1ibc6XPEj8bTy8Y="


def test_encrypt_decrypt_roundtrip():
    token = crypto.encrypt(b"secret")
    assert token != b"secret"
    assert crypto.decrypt(token) == b"secret"


def test_decrypt_with_wrong_key_raises(settings):
    token = crypto.encrypt(b"secret")
    settings.FIELD_ENCRYPTION_KEY = SECOND_VALID_KEY
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


def test_committed_dev_key_is_valid(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "")
    import config.settings.base as base

    importlib.reload(base)
    import config.settings.dev as dev

    importlib.reload(dev)
    Fernet(dev.FIELD_ENCRYPTION_KEY.encode())


def test_env_example_key_is_valid():
    text = Path(".env.example").read_text()
    match = re.search(r"^FIELD_ENCRYPTION_KEY=(.+)$", text, re.MULTILINE)
    assert match is not None
    Fernet(match.group(1).strip().encode())
