import base64
from dataclasses import dataclass

import base58
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from actors import crypto
from actors.models import Actor

ED25519_MULTICODEC_PREFIX = b"\xed\x01"


@dataclass(frozen=True)
class ActorKeyMaterial:
    rsa_private_key: rsa.RSAPrivateKey
    rsa_public_key: rsa.RSAPublicKey
    ed25519_private_key: ed25519.Ed25519PrivateKey
    ed25519_public_key: ed25519.Ed25519PublicKey


def load_actor_keys(actor: Actor) -> ActorKeyMaterial:
    rsa_private_pem = crypto.decrypt(bytes(actor.private_key_pem))
    rsa_private_key = serialization.load_pem_private_key(rsa_private_pem, password=None)
    ed25519_private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
        crypto.decrypt(bytes(actor.ed25519_private_key))
    )
    return ActorKeyMaterial(
        rsa_private_key=rsa_private_key,
        rsa_public_key=rsa_private_key.public_key(),
        ed25519_private_key=ed25519_private_key,
        ed25519_public_key=ed25519_private_key.public_key(),
    )


def rsa_public_key_from_actor(actor: Actor) -> rsa.RSAPublicKey:
    key = serialization.load_pem_public_key(actor.public_key_pem.encode("ascii"))
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError("Actor public key is not RSA")
    return key


def ed25519_public_key_from_actor(actor: Actor) -> ed25519.Ed25519PublicKey:
    raw = base64.b64decode(actor.ed25519_public_key)
    return ed25519.Ed25519PublicKey.from_public_bytes(raw)


def multibase_base58btc(data: bytes) -> str:
    return "z" + base58.b58encode(data).decode("ascii")


def ed25519_multikey(public_bytes: bytes) -> str:
    return multibase_base58btc(ED25519_MULTICODEC_PREFIX + public_bytes)
