import copy
import hashlib
from datetime import UTC, datetime

import base58
import jcs
from cryptography.exceptions import InvalidSignature

from actors.models import Actor
from federation.keys import ed25519_public_key_from_actor, load_actor_keys

PROOF_TYPE = "DataIntegrityProof"
CRYPTOSUITE = "eddsa-jcs-2022"
PROOF_PURPOSE = "assertionMethod"


def _hash(value):
    return hashlib.sha256(jcs.canonicalize(value)).digest()


def _hash_data(document, proof_config):
    return _hash(proof_config) + _hash(document)


def _encode_signature(signature):
    return "z" + base58.b58encode(signature).decode("ascii")


def _decode_signature(value):
    if not isinstance(value, str) or not value.startswith("z"):
        raise ValueError("invalid proofValue")
    return base58.b58decode(value[1:])


def attach_proof(document, actor):
    secured = copy.deepcopy(document)
    secured.pop("proof", None)
    proof = {
        "type": PROOF_TYPE,
        "cryptosuite": CRYPTOSUITE,
        "created": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "verificationMethod": f"{actor.ap_id}#ed25519-key",
        "proofPurpose": PROOF_PURPOSE,
    }
    if "@context" in secured:
        proof["@context"] = copy.deepcopy(secured["@context"])
    signature = load_actor_keys(actor).ed25519_private_key.sign(
        _hash_data(secured, proof)
    )
    proof["proofValue"] = _encode_signature(signature)
    secured["proof"] = proof
    return secured


def _context_matches(actual, expected):
    if isinstance(expected, list):
        return isinstance(actual, list) and actual[: len(expected)] == expected
    return actual == expected


def verify_proof(document):
    proof = document.get("proof")
    if not isinstance(proof, dict):
        return False
    if proof.get("type") != PROOF_TYPE:
        return False
    if proof.get("cryptosuite") != CRYPTOSUITE:
        return False
    if proof.get("proofPurpose") != PROOF_PURPOSE:
        return False
    unsecured = copy.deepcopy(document)
    unsecured.pop("proof", None)
    proof_config = {key: value for key, value in proof.items() if key != "proofValue"}
    if "@context" in proof_config:
        if not _context_matches(document.get("@context"), proof_config["@context"]):
            return False
        unsecured["@context"] = copy.deepcopy(proof_config["@context"])
    method = proof.get("verificationMethod", "")
    actor = Actor.objects.filter(ap_id=str(method).split("#", 1)[0]).first()
    if actor is None:
        return False
    try:
        signature = _decode_signature(proof.get("proofValue"))
    except ValueError, TypeError:
        return False
    try:
        ed25519_public_key_from_actor(actor).verify(
            signature, _hash_data(unsecured, proof_config)
        )
    except InvalidSignature:
        return False
    return True
