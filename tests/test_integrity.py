from uuid import uuid4

import pytest
from django.urls import reverse

from actors.services import create_local_actor
from federation import integrity
from federation.delivery import fan_out
from federation.models import Delivery
from tests.test_reader import _publish, _scene


def _document():
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": "https://testserver/objects/1",
        "type": "Note",
        "content": "Hello",
    }


@pytest.mark.django_db
def test_attach_and_verify_proof_round_trip():
    actor = create_local_actor(f"signer{uuid4().hex[:6]}")
    secured = integrity.attach_proof(_document(), actor)
    proof = secured["proof"]
    assert proof["type"] == "DataIntegrityProof"
    assert proof["cryptosuite"] == "eddsa-jcs-2022"
    assert proof["proofPurpose"] == "assertionMethod"
    assert proof["proofValue"].startswith("z")
    assert integrity.verify_proof(secured) is True


@pytest.mark.django_db
def test_tampering_and_wrong_signer_fail():
    actor = create_local_actor(f"signer{uuid4().hex[:6]}")
    other = create_local_actor(f"other{uuid4().hex[:6]}")
    secured = integrity.attach_proof(_document(), actor)
    tampered = dict(secured)
    tampered["content"] = "Changed"
    assert integrity.verify_proof(tampered) is False

    wrong = dict(secured)
    wrong["proof"] = dict(secured["proof"])
    wrong["proof"]["verificationMethod"] = f"{other.ap_id}#ed25519-key"
    assert integrity.verify_proof(wrong) is False
    assert integrity.verify_proof(_document()) is False


@pytest.mark.django_db
def test_outbound_delivery_is_signed():
    actor = create_local_actor(f"signer{uuid4().hex[:6]}")
    activity = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"https://testserver/activities/{uuid4()}",
        "type": "Create",
        "actor": actor.ap_id,
        "object": _document(),
    }
    fan_out(activity, ["https://peer.test/inbox"], actor)
    delivery = Delivery.objects.get()
    assert "proof" in delivery.activity
    assert integrity.verify_proof(delivery.activity) is True


@pytest.mark.django_db
def test_served_page_note_is_signed(client):
    _, _, _, _, page, _, _ = _scene()
    _publish(page)
    response = client.get(reverse("page-detail", args=[page.id]))
    assert response.status_code == 200
    note = response.json()
    assert note["proof"]["cryptosuite"] == "eddsa-jcs-2022"
    assert integrity.verify_proof(note) is True
