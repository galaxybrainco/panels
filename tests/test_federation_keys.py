import base64

import base58
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from actors.services import create_local_actor
from federation.keys import (
    ED25519_MULTICODEC_PREFIX,
    ed25519_multikey,
    ed25519_public_key_from_actor,
    load_actor_keys,
    multibase_base58btc,
    rsa_public_key_from_actor,
)


@pytest.mark.django_db
def test_load_actor_keys_matches_actor_public_material():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    assert isinstance(keys.rsa_private_key, rsa.RSAPrivateKey)
    assert isinstance(keys.ed25519_private_key, ed25519.Ed25519PrivateKey)
    assert isinstance(keys.rsa_public_key, rsa.RSAPublicKey)
    assert isinstance(keys.ed25519_public_key, ed25519.Ed25519PublicKey)


@pytest.mark.django_db
def test_public_key_helpers_match_actor():
    actor = create_local_actor("alice")
    rsa_key = rsa_public_key_from_actor(actor)
    assert (
        rsa_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        == actor.public_key_pem
    )

    ed_key = ed25519_public_key_from_actor(actor)
    raw = ed_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert base64.b64encode(raw).decode() == actor.ed25519_public_key


def test_multibase_base58btc_roundtrip():
    encoded = multibase_base58btc(b"\x01\x02\x03")
    assert encoded.startswith("z")
    assert base58.b58decode(encoded[1:]) == b"\x01\x02\x03"


def test_ed25519_multikey_has_multicodec_prefix():
    public = ed25519.Ed25519PrivateKey.generate().public_key()
    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    multikey = ed25519_multikey(raw)
    assert multikey.startswith("z")
    assert base58.b58decode(multikey[1:]) == ED25519_MULTICODEC_PREFIX + raw
