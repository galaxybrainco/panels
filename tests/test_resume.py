import pytest
from django.urls import reverse

from comics.models import Page, PageStatus
from federation.activitypub import Audience
from social.models import ReadingProgress
from tests.test_reader import _publish, _reader, _scene, _user


@pytest.mark.django_db
def test_authenticated_reading_records_and_updates_progress(client):
    _, _, _, _, p1, p2, _ = _scene()
    _publish(p1, p2)
    user = _user()
    client.force_login(user)

    client.get(_reader(p1))
    progress = ReadingProgress.objects.get(user=user)
    assert progress.page == p1

    client.get(_reader(p2))
    progress.refresh_from_db()
    assert progress.page == p2
    assert ReadingProgress.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_anonymous_reading_records_nothing(client):
    _, _, _, _, p1, _, _ = _scene()
    _publish(p1)
    client.get(_reader(p1))
    assert ReadingProgress.objects.count() == 0


@pytest.mark.django_db
def test_htmx_reading_records_progress(client):
    _, _, _, _, p1, _, _ = _scene()
    _publish(p1)
    user = _user()
    client.force_login(user)
    client.get(_reader(p1), HTTP_HX_REQUEST="true")
    assert ReadingProgress.objects.get(user=user).page == p1


@pytest.mark.django_db
def test_home_shows_continue_for_progress(client):
    _, comic, _, _, p1, _, _ = _scene()
    _publish(p1)
    user = _user()
    client.force_login(user)
    client.get(_reader(p1))
    response = client.get(reverse("comic-home", args=[comic.slug]))
    assert b"Continue:" in response.content
    assert _reader(p1).encode() in response.content


@pytest.mark.django_db
def test_archive_marks_last_read_page(client):
    _, comic, first, _, p1, p2, _ = _scene()
    _publish(p1, p2)
    user = _user()
    client.force_login(user)
    client.get(_reader(p1))
    response = client.get(reverse("series-archive", args=[comic.slug, first.slug]))
    assert b'aria-current="page"' in response.content
    assert b"Continue reading:" in response.content


@pytest.mark.django_db
def test_denied_gated_read_does_not_record(client):
    _, _, _, _, p1, _, _ = _scene(audience=Audience.MEMBERS)
    _publish(p1)
    client.force_login(_user())
    assert client.get(_reader(p1)).status_code == 403
    assert ReadingProgress.objects.count() == 0


@pytest.mark.django_db
def test_progress_for_unpublished_page_is_hidden(client):
    _, comic, _, _, p1, _, _ = _scene()
    _publish(p1)
    user = _user()
    client.force_login(user)
    client.get(_reader(p1))
    Page.objects.filter(pk=p1.pk).update(status=PageStatus.DRAFT)
    response = client.get(reverse("comic-home", args=[comic.slug]))
    assert b"Continue:" not in response.content
    assert ReadingProgress.objects.filter(user=user).exists()
