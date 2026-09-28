import pytest

from actors.services import create_local_actor
from federation.keys import ed25519_public_key_from_actor, load_actor_keys
from federation.proofs import (
    DATA_INTEGRITY_CONTEXT,
    _jcs_sha256,
    add_integrity_proof,
    verify_integrity_proof,
)


def _document():
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": "https://panels.test/objects/1",
        "type": "Note",
        "attributedTo": "https://panels.test/actors/alice",
        "content": "Hello world",
    }


def test_jcs_sha256_is_deterministic_for_key_order():
    assert _jcs_sha256({"b": 1, "a": 2}) == _jcs_sha256({"a": 2, "b": 1})


@pytest.mark.django_db
def test_add_and_verify_integrity_proof():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_integrity_proof(_document(), actor, keys)
    proof = signed["proof"]
    assert proof["type"] == "DataIntegrityProof"
    assert proof["cryptosuite"] == "eddsa-jcs-2022"
    assert proof["proofPurpose"] == "assertionMethod"
    assert proof["verificationMethod"] == f"{actor.ap_id}#ed25519-key"
    assert proof["proofValue"].startswith("z")
    assert DATA_INTEGRITY_CONTEXT in signed["@context"]
    assert verify_integrity_proof(signed, ed25519_public_key_from_actor(actor)) is True


@pytest.mark.django_db
def test_verify_rejects_tampered_content():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_integrity_proof(_document(), actor, keys)
    signed["content"] = "Tampered"
    assert verify_integrity_proof(signed, ed25519_public_key_from_actor(actor)) is False


@pytest.mark.django_db
def test_verify_rejects_unknown_cryptosuite():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_integrity_proof(_document(), actor, keys)
    signed["proof"]["cryptosuite"] = "eddsa-rdfc-2022"
    assert verify_integrity_proof(signed, ed25519_public_key_from_actor(actor)) is False


@pytest.mark.django_db
def test_add_integrity_proof_is_deterministic_with_fixed_created():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    created = "2026-09-28T12:00:00Z"
    first = add_integrity_proof(_document(), actor, keys, created=created)
    second = add_integrity_proof(_document(), actor, keys, created=created)
    assert first == second
