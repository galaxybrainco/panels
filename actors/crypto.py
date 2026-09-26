from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from django.conf import settings


class KeyEncryptionError(Exception):
    pass


def _fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        raise KeyEncryptionError("FIELD_ENCRYPTION_KEY is not set")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise KeyEncryptionError(
            "FIELD_ENCRYPTION_KEY is not a valid Fernet key"
        ) from exc


def encrypt(plaintext: bytes) -> bytes:
    return _fernet().encrypt(plaintext)


def decrypt(ciphertext: bytes) -> bytes:
    try:
        return _fernet().decrypt(ciphertext)
    except (InvalidToken, ValueError, TypeError) as exc:
        raise KeyEncryptionError("Could not decrypt key material") from exc


def generate_rsa_keypair() -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_bytes, public_bytes
