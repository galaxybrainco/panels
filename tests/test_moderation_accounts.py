from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from actors.models import Actor, ActorType, Instance
from actors.services import create_local_actor
from comics import publishing
from comics.models import Page, PageStatus
from comics.publishing import publish_page
from comics.services import create_comic, create_page, create_series
from federation.delivery import fan_out
from federation.models import Activity, Delivery
from federation.remotes import fetch_remote_actor
from moderation import accounts, domains
from moderation.models import AccountState, ModerationAudit
from social.comments import federatable_page
from social.handlers import handle_follow
from social.models import Follow, FollowStatus
from social.services import follow, follower_inboxes


def _user(staff=False):
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x", is_staff=staff
    )


def _local(user=None):
    user = user or _user()
    return user, create_local_actor(f"u{uuid4().hex[:8]}", user=user)


def _remote(handle="bob"):
    return Actor.objects.create(
        ap_id=f"https://remote.test/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain="remote.test",
        inbox=f"https://remote.test/actors/{handle}/inbox",
    )


@pytest.mark.django_db
def test_limit_and_suspend_set_state_and_audit():
    staff = _user(staff=True)
    _, actor = _local()
    accounts.apply_account_action(actor, staff, "limit", reason="spam")
    assert accounts.account_state(actor) == AccountState.LIMITED
    accounts.apply_account_action(actor, staff, "suspend", reason="spam")
    assert accounts.is_suspended(actor) is True
    assert ModerationAudit.objects.filter(action="account.suspended").count() == 1


@pytest.mark.django_db
def test_freeze_is_local_only_and_blocks_posting():
    staff = _user(staff=True)
    remote = _remote()
    with pytest.raises(accounts.LocalOnlyError):
        accounts.apply_account_action(remote, staff, "freeze", reason="x")
    user, actor = _local()
    accounts.apply_account_action(actor, staff, "freeze", reason="x")
    assert accounts.is_frozen(actor) is True
    assert accounts.can_post(user) is False


@pytest.mark.django_db
def test_disable_and_restore_reactivate_the_user():
    staff = _user(staff=True)
    user, actor = _local()
    accounts.apply_account_action(actor, staff, "disable", reason="abuse")
    user.refresh_from_db()
    assert user.is_active is False
    accounts.apply_account_action(actor, staff, "restore", reason="appeal")
    user.refresh_from_db()
    assert user.is_active is True
    assert accounts.account_state(actor) == AccountState.NORMAL


@pytest.mark.django_db
def test_force_sensitive_marks_existing_pages():
    staff = _user(staff=True)
    user, actor = _local()
    comic = create_comic(user, f"comic{uuid4().hex[:6]}", "Comic")
    series = create_series(user, comic, "Main")
    page = create_page(user, series, title="Page")
    page.published_by = user
    page.save(update_fields=["published_by", "updated_at"])
    accounts.apply_account_action(
        actor, staff, "force_sensitive", reason="policy", enabled=True
    )
    page.refresh_from_db()
    assert page.sensitive is True
    assert accounts.is_force_sensitive(user) is True


@pytest.mark.django_db
def test_warn_sends_one_email():
    staff = _user(staff=True)
    user, actor = _local()
    accounts.apply_account_action(actor, staff, "warn", reason="be kind")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert ModerationAudit.objects.filter(action="account.warned").count() == 1


@pytest.mark.django_db
def test_actions_require_reason_and_staff():
    staff = _user(staff=True)
    _, actor = _local()
    with pytest.raises(accounts.InvalidActionError):
        accounts.apply_account_action(actor, staff, "limit", reason="  ")
    with pytest.raises(accounts.NotPermittedError):
        accounts.apply_account_action(actor, _user(), "limit", reason="x")
    with pytest.raises(accounts.InvalidActionError):
        accounts.apply_account_action(actor, staff, "bogus", reason="x")


@pytest.mark.django_db
def test_account_endpoints_are_staff_only(client):
    staff = _user(staff=True)
    _, actor = _local()
    client.force_login(_user())
    assert client.get(f"/moderation/accounts/{actor.id}").status_code == 403
    client.force_login(staff)
    response = client.post(
        f"/moderation/accounts/{actor.id}/action",
        data='{"action": "limit", "reason": "spam"}',
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["state"] == "limited"
    assert client.get(f"/moderation/accounts/{actor.id}").json()["state"] == "limited"
    remote = _remote()
    assert (
        client.post(
            f"/moderation/accounts/{remote.id}/action",
            data='{"action": "freeze", "reason": "x"}',
            content_type="application/json",
        ).status_code
        == 409
    )


@pytest.mark.django_db
def test_suspended_accounts_are_dropped_inbound():
    _, actor = _local()
    staff = _user(staff=True)
    accounts.apply_account_action(actor, staff, "suspend", reason="x")
    assert accounts.should_drop_inbound(actor) is True


@pytest.mark.django_db
def test_allowlist_mode_drops_non_allowlisted_domains(settings):
    settings.FEDERATION_ALLOWLIST_ENABLED = True
    remote = _remote()
    assert accounts.should_drop_inbound(remote) is True
    Instance.objects.create(domain="remote.test", allowlisted=True)
    assert accounts.should_drop_inbound(remote) is False


@pytest.mark.django_db
def test_blocked_domain_delivery_is_skipped():
    _, actor = _local()
    remote = _remote()
    Instance.objects.create(domain="remote.test", blocked=True)
    activity = Activity.objects.create(
        ap_id="https://testserver/activities/1", type="Create", actor=actor, payload={}
    )
    assert fan_out(activity.payload, [remote.inbox], actor) == []
    assert Delivery.objects.count() == 0


@pytest.mark.django_db
def test_suspended_followers_are_excluded_and_following_suspended_is_rejected():
    _, actor = _local()
    remote_follower = _remote(handle="fan")
    Follow.objects.create(
        follower=remote_follower, target=actor, status=FollowStatus.ACCEPTED
    )
    staff = _user(staff=True)
    accounts.apply_account_action(remote_follower, staff, "suspend", reason="x")
    assert follower_inboxes(actor) == []
    target = _local()[1]
    accounts.apply_account_action(target, staff, "suspend", reason="x")
    with pytest.raises(ValidationError):
        follow(actor, target)


@pytest.mark.django_db
def test_public_pages_hide_suspended_content(client):
    user, actor = _local()
    comic = create_comic(user, f"comic{uuid4().hex[:6]}", "Comic")
    series = create_series(user, comic, "Main")
    page = create_page(user, series, title="Page")
    Page.objects.filter(pk=page.pk).update(
        status="published",
        ap_id=f"https://testserver/pages/{page.id}",
        published_at=timezone.now(),
    )
    staff = _user(staff=True)
    accounts.apply_account_action(comic.actor, staff, "suspend", reason="x")
    assert client.get(f"/pages/{page.id}").status_code == 404


@pytest.mark.django_db
def test_frozen_users_cannot_publish():
    user, actor = _local()
    comic = create_comic(user, f"comic{uuid4().hex[:6]}", "Comic")
    series = create_series(user, comic, "Main")
    page = create_page(user, series, title="Page")
    staff = _user(staff=True)
    accounts.apply_account_action(actor, staff, "freeze", reason="x")
    with pytest.raises(PermissionDenied):
        publish_page(user, page)


@pytest.mark.django_db
def test_domain_policy_normalizes_url_hosts_and_subdomains():
    staff = _user(staff=True)
    domains.apply_domain_action("remote.test", staff, "suspend")
    assert accounts.domain_host("https://Remote.Test:8443/actors/bob") == "remote.test"
    assert accounts.allowed_domain("https://Remote.Test:8443/actors/bob") is False
    assert accounts.allowed_domain("sub.remote.test") is False
    assert accounts.allowed_domain("https://other.test/inbox") is True


@pytest.mark.django_db
def test_blocked_domain_refuses_fetch_for_uppercase_and_port():
    cache.clear()
    Instance.objects.create(domain="remote.test", blocked=True)
    assert fetch_remote_actor("https://Remote.Test:8443/actors/bob") is None


@override_settings(
    INSTANCE_URL="https://panels.example.com", INSTANCE_DOMAIN="panels.example.com"
)
@pytest.mark.django_db
def test_local_domain_cannot_be_moderated():
    with pytest.raises(domains.InvalidDomainError):
        domains.apply_domain_action("Panels.Example.com", _user(staff=True), "suspend")


@pytest.mark.django_db
def test_scheduled_publish_refuses_suspended_author():
    staff = _user(staff=True)
    user = _user()
    personal = create_local_actor(f"u{uuid4().hex[:8]}", user=user)
    comic = create_comic(user, f"comic{uuid4().hex[:6]}", "Comic")
    series = create_series(user, comic, "Main")
    page = create_page(user, series, title="Page")
    Page.objects.filter(pk=page.pk).update(
        status=PageStatus.SCHEDULED, scheduled_by=user
    )
    accounts.apply_account_action(personal, staff, "suspend", reason="x")
    page.refresh_from_db()
    with pytest.raises(ValidationError):
        publishing.publish(page, published_by=user)
    page.refresh_from_db()
    assert page.status == PageStatus.SCHEDULED


def _suspended_local():
    staff = _user(staff=True)
    user, actor = _local()
    accounts.apply_account_action(actor, staff, "suspend", reason="x")
    return actor


@pytest.mark.django_db
def test_suspended_local_actor_endpoints_are_hidden(client):
    actor = _suspended_local()
    handle = actor.handle
    assert (
        client.get(reverse("actor-detail", kwargs={"handle": handle})).status_code
        == 404
    )
    assert (
        client.get(
            reverse("webfinger"), {"resource": f"acct:{handle}@testserver"}
        ).status_code
        == 404
    )
    assert (
        client.post(reverse("actor-inbox", kwargs={"handle": handle})).status_code
        == 404
    )


@pytest.mark.django_db
def test_follow_to_suspended_target_is_ignored():
    target = _suspended_local()
    remote = _remote()
    activity = SimpleNamespace(
        actor=remote,
        ap_id="https://remote.test/activities/follow",
        payload={"type": "Follow", "object": target.ap_id},
    )
    handle_follow(activity)
    assert not Follow.objects.filter(target=target).exists()


@pytest.mark.django_db
def test_federatable_page_hides_suspended_comic():
    staff = _user(staff=True)
    user = _user()
    comic = create_comic(user, f"comic{uuid4().hex[:6]}", "Comic")
    series = create_series(user, comic, "Main")
    page = create_page(user, series, title="Page")
    ap_id = f"https://testserver/pages/{page.id}"
    Page.objects.filter(pk=page.pk).update(status=PageStatus.PUBLISHED, ap_id=ap_id)
    accounts.apply_account_action(comic.actor, staff, "suspend", reason="x")
    assert federatable_page(ap_id) is None
