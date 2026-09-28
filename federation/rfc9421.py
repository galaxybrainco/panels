from http_message_signatures import (
    HTTPMessageSigner,
    HTTPMessageVerifier,
    HTTPSignatureKeyResolver,
    algorithms,
)

DEFAULT_COVERED_COMPONENTS = ("@method", "@target-uri", "content-digest")
DEFAULT_REQUIRED_COMPONENTS = ("@method", "@target-uri")


class DictKeyResolver(HTTPSignatureKeyResolver):
    def __init__(self, private_keys=None, public_keys=None):
        self._private_keys = private_keys or {}
        self._public_keys = public_keys or {}

    def resolve_private_key(self, key_id):
        return self._private_keys[key_id]

    def resolve_public_key(self, key_id):
        return self._public_keys[key_id]


class CallbackKeyResolver(HTTPSignatureKeyResolver):
    def __init__(self, resolve_public_key):
        self._resolve_public_key = resolve_public_key

    def resolve_private_key(self, key_id):
        raise KeyError(key_id)

    def resolve_public_key(self, key_id):
        key = self._resolve_public_key(key_id)
        if key is None:
            raise KeyError(key_id)
        return key


def sign_rfc9421(message, private_key, key_id, covered_component_ids=None):
    resolver = DictKeyResolver(private_keys={key_id: private_key})
    signer = HTTPMessageSigner(
        signature_algorithm=algorithms.RSA_V1_5_SHA256, key_resolver=resolver
    )
    signer.sign(
        message,
        key_id=key_id,
        covered_component_ids=list(covered_component_ids or DEFAULT_COVERED_COMPONENTS),
    )
    return message


def verify_rfc9421(message, resolve_public_key, required_components=None) -> bool:
    resolver = CallbackKeyResolver(resolve_public_key)
    verifier = HTTPMessageVerifier(
        signature_algorithm=algorithms.RSA_V1_5_SHA256, key_resolver=resolver
    )
    try:
        results = verifier.verify(message)
    except Exception:
        return False
    required = {
        component.strip('"')
        for component in (required_components or DEFAULT_REQUIRED_COMPONENTS)
    }
    if message.headers.get("Content-Digest"):
        required.add("content-digest")
    return any(
        required.issubset(
            {component.strip('"') for component in result.covered_components}
        )
        for result in results
    )
