import uuid

import pytest
from django.contrib.auth import get_user_model

from actors.models import Instance
from actors.services import create_local_actor
from comics.models import Chapter, ComicRole, Tag
from comics.services import create_comic, create_page, create_series
from federation.models import (
    Activity,
    Delivery,
    PeerSignaturePreference,
    SignatureScheme,
)


def _user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email, password="x")


@pytest.mark.django_db
def test_entity_models_use_uuid_primary_keys_with_timestamps():
    owner = _user()
    actor = create_local_actor("keyholder")
    instance = Instance.objects.create(domain="other.test")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon", tags=["comedy"])
    series = create_series(owner, comic, "Main Story")
    chapter = Chapter.objects.create(series=series, title="One", position=1)
    page = create_page(owner, series)
    tag = Tag.objects.get(name="comedy")
    delivery = Delivery.objects.create(
        inbox_url="https://other.test/inbox", activity={"type": "Create"}, actor=actor
    )
    activity = Activity.objects.create(
        ap_id="https://other.test/activities/1",
        type="Create",
        payload={"type": "Create"},
    )

    entities = [
        owner,
        actor,
        instance,
        comic,
        series,
        chapter,
        page,
        tag,
        delivery,
        activity,
    ]
    for instance_obj in entities:
        assert isinstance(instance_obj.pk, uuid.UUID), type(instance_obj).__name__
        assert instance_obj.created_at is not None, type(instance_obj).__name__
        assert instance_obj.updated_at is not None, type(instance_obj).__name__


@pytest.mark.django_db
def test_join_and_config_tables_keep_integer_primary_keys():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    role = ComicRole.objects.get(comic=comic, user=owner)
    preference = PeerSignaturePreference.objects.create(
        domain="other.test", scheme=SignatureScheme.CAVAGE
    )
    for instance_obj in (role, preference):
        assert isinstance(instance_obj.pk, int), type(instance_obj).__name__
        assert not isinstance(instance_obj.pk, uuid.UUID)


@pytest.mark.django_db
def test_user_model_keeps_date_joined():
    user = _user()
    assert user.date_joined is not None
    assert user.created_at is not None
