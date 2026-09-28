import hashlib
from datetime import UTC, datetime

import base58
import jcs

from actors.models import Actor
from federation.keys import ActorKeyMaterial, multibase_base58btc

DATA_INTEGRITY_CONTEXT = "https://w3id.org/security/data-integrity/v2"
EDDSA_JCS_2022 = "eddsa-jcs-2022"


def _jcs_sha256(document: dict) -> bytes:
    return hashlib.sha256(jcs.canonicalize(document)).digest()


def now_isoformat() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _parse_isoformat(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def ensure_data_integrity_context(document: dict) -> dict:
    context = document.get("@context")
    if context is None:
        return {"@context": DATA_INTEGRITY_CONTEXT, **document}
    if isinstance(context, str):
        context = [context]
    if DATA_INTEGRITY_CONTEXT in context:
        return document
    return {**document, "@context": [*context, DATA_INTEGRITY_CONTEXT]}


def add_integrity_proof(
    document, actor: Actor, keys: ActorKeyMaterial, created=None, expires=None
):
    secured = ensure_data_integrity_context(document)
    proof = {
        "type": "DataIntegrityProof",
        "cryptosuite": EDDSA_JCS_2022,
        "created": created or now_isoformat(),
        "verificationMethod": f"{actor.ap_id}#ed25519-key",
        "proofPurpose": "assertionMethod",
    }
    if expires is not None:
        proof["expires"] = expires
    digest = _jcs_sha256(proof) + _jcs_sha256(secured)
    proof["proofValue"] = multibase_base58btc(keys.ed25519_private_key.sign(digest))
    return {**secured, "proof": proof}


def verify_integrity_proof(document, public_key, *, now=None) -> bool:
    proof = document.get("proof")
    if not isinstance(proof, dict):
        return False
    if proof.get("type") != "DataIntegrityProof":
        return False
    if proof.get("cryptosuite") != EDDSA_JCS_2022:
        return False
    proof_value = proof.get("proofValue")
    if not proof_value or not proof_value.startswith("z"):
        return False
    expires = proof.get("expires")
    if expires is not None:
        now = now or datetime.now(tz=UTC)
        if _parse_isoformat(expires) < now:
            return False
    pure_proof = {k: v for k, v in proof.items() if k != "proofValue"}
    pure_document = {k: v for k, v in document.items() if k != "proof"}
    digest = _jcs_sha256(pure_proof) + _jcs_sha256(pure_document)
    try:
        signature = base58.b58decode(proof_value[1:])
        public_key.verify(signature, digest)
    except Exception:
        return False
    return True
