import json
from pathlib import Path

from pyld import jsonld

CONTEXT_DIR = Path(__file__).resolve().parent / "jsonld_contexts"
CONTEXT_FILES = {
    "https://www.w3.org/ns/activitystreams": "activitystreams.json",
    "https://w3id.org/security/v1": "security-v1.json",
    "https://w3id.org/identity/v1": "identity-v1.json",
    "http://w3id.org/identity/v1": "identity-v1.json",
}


def _document_loader(url, options=None):
    filename = CONTEXT_FILES.get(url)
    if filename is None:
        raise ValueError(f"No vendored JSON-LD context for {url}")
    document = json.loads((CONTEXT_DIR / filename).read_text(encoding="utf-8"))
    return {"contextUrl": None, "documentUrl": url, "document": document}


def install_document_loader() -> None:
    jsonld.set_document_loader(_document_loader)


def canonicalize(document: dict) -> str:
    install_document_loader()
    return jsonld.normalize(
        document, {"algorithm": "URDNA2015", "format": "application/n-quads"}
    )


def compact(document: dict, context) -> dict:
    install_document_loader()
    return jsonld.compact(document, context)
