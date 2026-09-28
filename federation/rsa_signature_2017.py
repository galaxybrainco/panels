import base64
import hashlib
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from federation.jsonld import canonicalize

RSA_SIGNATURE_2017 = "RsaSignature2017"
IDENTITY_CONTEXT = "https://w3id.org/identity/v1"
SECURITY_CONTEXT = "https://w3id.org/security/v1"
OMITTED_OPTION_KEYS = ("type", "id", "signatureValue")


def _now_isoformat() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _sha256_hex(document: dict) -> str:
    return hashlib.sha256(canonicalize(document).encode("utf-8")).hexdigest()


def _options_hash(options: dict) -> str:
    filtered = {k: v for k, v in options.items() if k not in OMITTED_OPTION_KEYS}
    return _sha256_hex({**filtered, "@context": IDENTITY_CONTEXT})


def add_rsa_signature_2017(document, private_key, creator, created=None, expires=None):
    created = created or _now_isoformat()
    if expires is None:
        expires = (
            datetime.now(tz=UTC).replace(microsecond=0) + timedelta(days=2)
        ).isoformat() + "Z"
    options = {
        "type": RSA_SIGNATURE_2017,
        "creator": creator,
        "created": created,
        "expires": expires,
    }
    document_without_signature = {k: v for k, v in document.items() if k != "signature"}
    to_sign = (_options_hash(options) + _sha256_hex(document_without_signature)).encode(
        "utf-8"
    )
    signature_value = base64.b64encode(
        private_key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
    ).decode("ascii")

    context = document.get("@context")
    if isinstance(context, str):
        context = [context]
    context = list(context or [])
    if SECURITY_CONTEXT not in context:
        context.append(SECURITY_CONTEXT)
    return {
        **document_without_signature,
        "@context": context,
        "signature": {**options, "signatureValue": signature_value},
    }


def verify_rsa_signature_2017(document, public_key) -> bool:
    signature = document.get("signature")
    if not isinstance(signature, dict):
        return False
    if signature.get("type") != RSA_SIGNATURE_2017:
        return False
    if "signatureValue" not in signature:
        return False
    document_without_signature = {k: v for k, v in document.items() if k != "signature"}
    to_verify = (
        _options_hash(signature) + _sha256_hex(document_without_signature)
    ).encode("utf-8")
    try:
        public_key.verify(
            base64.b64decode(signature["signatureValue"]),
            to_verify,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        return False
    return True
