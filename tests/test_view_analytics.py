import pytest
from django.urls import reverse

from comics.models import PageView
from federation.activitypub import Audience
from tests.test_reader import _publish, _reader, _scene, _user


@pytest.mark.django_db
def test_reading_records_views_for_users_and_sessions(client):
    _, _, _, _, page, _, _ = _scene()
    _publish(page)
    user = _user()
    client.force_login(user)
    client.get(_reader(page))
    view = PageView.objects.get()
    assert view.page == page
    assert view.user == user
    assert len(view.session_hash) == 64

    client.get(_reader(page), HTTP_HX_REQUEST="true")
    assert PageView.objects.filter(page=page).count() == 2


@pytest.mark.django_db
def test_anonymous_views_use_a_hashed_session(client):
    _, _, _, _, page, _, _ = _scene()
    _publish(page)
    client.get(_reader(page))
    view = PageView.objects.get()
    assert view.user is None
    assert len(view.session_hash) == 64


@pytest.mark.django_db
def test_denied_or_hidden_pages_do_not_record_views(client):
    _, _, _, _, page, _, _ = _scene(audience=Audience.MEMBERS)
    _publish(page)
    assert client.get(_reader(page)).status_code == 302
    assert PageView.objects.count() == 0
    client.force_login(_user())
    assert client.get(_reader(page)).status_code == 403
    assert PageView.objects.count() == 0

    _, _, _, _, draft, _, _ = _scene()
    assert client.get(_reader(draft)).status_code == 404
    assert PageView.objects.count() == 0


@pytest.mark.django_db
def test_dashboard_shows_view_stats_and_top_pages(client):
    owner, comic, _, _, page, _, _ = _scene()
    _publish(page)
    for _ in range(3):
        fan = _user()
        client.force_login(fan)
        client.get(_reader(page))
    client.force_login(owner)
    response = client.get(reverse("comic-dashboard", args=[comic.slug]))
    assert response.context["views_total"] == 3
    assert response.context["views_30d"] == 3
    assert response.context["views_unique_30d"] == 3
    assert b"views (30d)" in response.content
    assert b"Top pages (30 days)" in response.content
