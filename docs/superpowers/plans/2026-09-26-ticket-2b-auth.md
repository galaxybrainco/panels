# Ticket 2b — Accounts & Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email/password accounts with mandatory verification, registration gated by `INSTANCE_OPEN_REGISTRATIONS`, a user-chosen handle that creates the user's Person actor at signup, optional MFA (TOTP, WebAuthn passkeys with passkey login, recovery codes), and production HTTPS/cookie hardening — all synchronous and tested.

**Architecture:** `django-allauth` (with the `[mfa]` extra) provides the auth flows against our custom email-identifier `User`; a custom `SignupForm` adds a validated `handle` field and creates the `Actor` via the existing `create_local_actor` service; a custom `AccountAdapter` gates signup on `INSTANCE_OPEN_REGISTRATIONS`; a minimal project override of `allauth/layouts/base.html` supplies the shared layout. All views remain synchronous Django.

**Tech Stack:** Django 6.1.1, PostgreSQL 18, `django-allauth[mfa]` 65.19.4 (brings `fido2`, `qrcode`), pytest-django. Added dependency: `django-allauth[mfa]==65.19.4`.

**Spec:** `webcomic-fediverse-plan.md` (§10 "Modern web accounts & auth", §5 NodeInfo registration status, §14 ticket 2 auth half). Ticket 2a (identity) is already merged; this is the auth half.

## Global Constraints

- Synchronous Django/WSGI only. No async in this plan.
- PostgreSQL only.
- Email is the **sole login identifier**; `User` has no username (`ACCOUNT_USER_MODEL_USERNAME_FIELD = None`).
- Email verification is **mandatory** before login.
- Public registration is open **only** when `INSTANCE_OPEN_REGISTRATIONS` is true; otherwise signup is closed.
- Signup requires a **user-chosen handle**: normalized to lowercase, `[a-z0-9_]`, 3–32 characters, not a reserved word, unique per instance. The user's `Actor` is created at signup with that handle.
- MFA is **optional**; passkey login, TOTP, and recovery codes are available.
- OAuth 2.1/OIDC provider remains deferred (no client API yet).
- Production must set `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, and HSTS.

## Review Focus

Spec-implied input classes and failure modes no task's happy path exercises; each is pinned by a test in its owning task:

- Signup succeeding while `INSTANCE_OPEN_REGISTRATIONS` is false (gate bypass) — Task 2.
- A signup creating a `User` but no `Actor`, or an `Actor` whose handle was never validated (invalid/reserved/duplicate reaching the DB) — Task 2.
- Logging in before the email address is verified, when verification is meant to be mandatory — Task 1.
- Passkey login advertised but misconfigured (missing type in `MFA_SUPPORTED_TYPES`, or WebAuthn unusable in local dev) — Task 3.
- Production serving without secure cookies, SSL redirect, or HSTS — Task 4.
- Auth emails (verification, password reset) failing to send or building links for the wrong host — Task 1.

## File Structure

- `accounts/adapter.py` — `AccountAdapter` gating signup on `INSTANCE_OPEN_REGISTRATIONS`.
- `accounts/forms.py` — `SignupForm` adding the validated `handle` and creating the actor.
- `actors/handles.py` — `normalize_handle`, `validate_handle`, `RESERVED_HANDLES`.
- `config/settings/base.py` — allauth apps/middleware/backends/settings, email config.
- `config/settings/dev.py` — console email, WebAuthn insecure-origin for localhost.
- `config/settings/prod.py` — HTTPS/cookie/HSTS hardening.
- `config/urls.py` — `include("allauth.urls")` under `accounts/`.
- `templates/allauth/layouts/base.html` — minimal project override of allauth's base layout.
- `tests/test_auth_login.py`, `tests/test_auth_signup.py`, `tests/test_auth_mfa.py`, `tests/test_handles.py` — project-level tests.
- `pyproject.toml` — add `django-allauth[mfa]`.

---

### Task 1: Install and configure allauth (email login, mandatory verification, layout)

**Files:**
- Modify: `pyproject.toml`, `config/settings/base.py`, `config/settings/dev.py`, `config/urls.py`, `.env.example`
- Create: `templates/allauth/layouts/base.html`
- Create: `tests/test_auth_login.py`

**Interfaces:**
- Consumes: the custom `accounts.User` (Ticket 2a).
- Produces: allauth URL names (`account_login`, `account_signup`, …), `AUTHENTICATION_BACKENDS`, and the auth settings later tasks extend; `templates/allauth/layouts/base.html` used by every auth page.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auth_login.py`:

```python
import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.urls import reverse

PASSWORD = "a-strong-passphrase-42"


def _make_user(email, *, verified):
    user = get_user_model().objects.create_user(email=email, password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=email, verified=verified, primary=True
    )
    return user


@pytest.mark.django_db
def test_login_page_renders(client):
    response = client.get(reverse("account_login"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_verified_user_can_log_in_with_email(client):
    _make_user("reader@example.com", verified=True)
    response = client.post(
        reverse("account_login"),
        {"login": "reader@example.com", "password": PASSWORD},
    )
    assert response.status_code == 302
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_unverified_user_cannot_log_in(client):
    _make_user("unverified@example.com", verified=False)
    client.post(
        reverse("account_login"),
        {"login": "unverified@example.com", "password": PASSWORD},
    )
    assert "_auth_user_id" not in client.session
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_auth_login.py -v`
Expected: FAIL/ERROR — `account_login` does not resolve and `allauth` is not installed.

- [ ] **Step 3: Add the dependency and install**

Modify `pyproject.toml` dependencies (keep alphabetical order):

```toml
dependencies = [
    "Django==6.1.1",
    "cryptography==50.0.1",
    "django-allauth[mfa]==65.19.4",
    "django-environ==0.14.0",
    "django-storages[s3]==1.14.6",
    "django-tasks-db==0.13.0",
    "psycopg[binary]==3.3.6",
]
```

Run: `uv sync`
Expected: `django-allauth`, `fido2`, `qrcode` are installed with no errors.

- [ ] **Step 4: Configure settings, URLs, and the layout**

Modify `config/settings/base.py`.

Add to `INSTALLED_APPS` after `"django.contrib.staticfiles"` (and before `"django_tasks_db"`):

```python
    "django.contrib.humanize",
    "allauth",
    "allauth.account",
    "allauth.mfa",
```

Append to `MIDDLEWARE`:

```python
    "allauth.account.middleware.AccountMiddleware",
```

Add a new auth block after the `AUTH_USER_MODEL` line:

```python
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_USER_MODEL_EMAIL_FIELD = "email"
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_UNIQUE_EMAIL = True
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=f"noreply@{INSTANCE_DOMAIN}")
MAILERS = {
    "default": {
        "BACKEND": env(
            "DJANGO_EMAIL_BACKEND",
            default="django.core.mail.backends.console.EmailBackend",
        ),
    },
}
```

Modify `config/settings/dev.py` — add the WebAuthn local-development allowance:

```python
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = True
```

Modify `config/urls.py` — add the allauth include:

```python
urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("healthz", healthz, name="healthz"),
    path("", include("actors.urls")),
]
```

Modify `.env.example` — add after `FIELD_ENCRYPTION_KEY`:

```text
DEFAULT_FROM_EMAIL=noreply@localhost
DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

Create `templates/allauth/layouts/base.html`:

```html
{% load i18n %}
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block head_title %}{% endblock %}</title>
  {% block extra_head %}{% endblock %}
</head>
<body>
  <header>
    <nav>
      {% if user.is_authenticated %}
        <a href="{% url 'mfa_index' %}">{% trans "Two-factor" %}</a>
        <a href="{% url 'account_email' %}">{% trans "Email" %}</a>
        <a href="{% url 'account_logout' %}">{% trans "Sign out" %}</a>
      {% else %}
        <a href="{% url 'account_login' %}">{% trans "Sign in" %}</a>
        <a href="{% url 'account_signup' %}">{% trans "Sign up" %}</a>
      {% endif %}
    </nav>
  </header>
  {% if messages %}
    <ul class="messages">
      {% for message in messages %}<li>{{ message }}</li>{% endfor %}
    </ul>
  {% endif %}
  <main>
    {% block content %}{% endblock %}
  </main>
  {% block extra_body %}{% endblock %}
</body>
</html>
```

- [ ] **Step 5: Apply migrations**

Run: `uv run python manage.py migrate`
Expected: allauth migrations apply with no errors.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_auth_login.py -v`
Expected: PASS — the login page renders, a verified user logs in, an unverified user does not.

- [ ] **Step 7: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green. Fix any findings before committing.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock config/settings/base.py config/settings/dev.py config/urls.py .env.example templates tests/test_auth_login.py
git commit -m "feat: install and configure django-allauth for email login"
```

---

### Task 2: Registration gate, handle validation, and actor creation

**Files:**
- Create: `accounts/adapter.py`, `accounts/forms.py`, `actors/handles.py`
- Modify: `config/settings/base.py`
- Create: `tests/test_handles.py`, `tests/test_auth_signup.py`

**Interfaces:**
- Consumes: `create_local_actor` (Ticket 2a), `Actor` (Ticket 2a), allauth's `SignupForm` (Task 1).
- Produces: `actors.handles.normalize_handle(str) -> str`, `actors.handles.validate_handle(str) -> str`, `RESERVED_HANDLES`; `accounts.forms.SignupForm`; `accounts.adapter.AccountAdapter`. Later tasks rely on signup producing a local `Actor`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_handles.py`:

```python
import pytest
from django.core.exceptions import ValidationError

from actors.handles import normalize_handle, validate_handle


@pytest.mark.parametrize("handle", ["alice", "a_b_1", "abc", "a" * 32])
def test_valid_handles_are_accepted(handle):
    assert validate_handle(handle) == handle


@pytest.mark.parametrize(
    "handle", ["ab", "a" * 33, "bad handle", "bad-handle", "bad.handle", ""]
)
def test_invalid_handles_are_rejected(handle):
    with pytest.raises(ValidationError):
        validate_handle(handle)


@pytest.mark.parametrize("handle", ["instance", "admin", "actors", "nodeinfo"])
def test_reserved_handles_are_rejected(handle):
    with pytest.raises(ValidationError):
        validate_handle(handle)


def test_handles_are_normalized_to_lowercase():
    assert normalize_handle("  Alice  ") == "alice"
    assert validate_handle("ALICE") == "alice"
```

Create `tests/test_auth_signup.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from actors.models import Actor

PASSWORD = "a-strong-passphrase-42"
SIGNUP_DATA = {
    "email": "reader@example.com",
    "password1": PASSWORD,
    "password2": PASSWORD,
    "handle": "alice",
}


def _post_signup(client, **overrides):
    data = {**SIGNUP_DATA, **overrides}
    return client.post(reverse("account_signup"), data)


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=False)
def test_signup_is_closed_when_registration_disabled(client):
    response = client.get(reverse("account_signup"))
    assert response.status_code == 200
    _post_signup(client)
    assert get_user_model().objects.count() == 0


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
def test_signup_creates_user_and_local_actor(client):
    response = _post_signup(client)
    assert response.status_code in (200, 302)
    user = get_user_model().objects.get(email="reader@example.com")
    actor = Actor.objects.get(handle="alice", domain="")
    assert actor.user == user
    assert actor.is_local
    assert actor.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
def test_signup_normalizes_handle(client):
    _post_signup(client, handle="ALICE")
    assert Actor.objects.filter(handle="alice", domain="").exists()


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
@pytest.mark.parametrize("handle", ["ab", "bad handle", "instance", "admin"])
def test_signup_rejects_invalid_or_reserved_handle(client, handle):
    _post_signup(client, handle=handle)
    assert get_user_model().objects.count() == 0
    assert Actor.objects.count() == 0


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
def test_signup_rejects_taken_handle(client):
    from actors.services import create_local_actor

    create_local_actor("alice")
    _post_signup(client)
    assert get_user_model().objects.count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_handles.py tests/test_auth_signup.py -v`
Expected: FAIL/ERROR — `actors.handles` does not exist, and signup currently ignores `handle` (creating a user without an actor).

- [ ] **Step 3: Implement handle validation**

Create `actors/handles.py`:

```python
import re

from django.core.exceptions import ValidationError

HANDLE_RE = re.compile(r"^[a-z0-9_]{3,32}$")

RESERVED_HANDLES = frozenset(
    {
        "about",
        "account",
        "accounts",
        "actor",
        "actors",
        "admin",
        "administrator",
        "api",
        "federation",
        "feeds",
        "healthz",
        "help",
        "inbox",
        "instance",
        "login",
        "media",
        "mod",
        "moderator",
        "nodeinfo",
        "outbox",
        "panels",
        "root",
        "signup",
        "staff",
        "static",
        "support",
        "system",
        "uploads",
        "webfinger",
    }
)


def normalize_handle(handle: str) -> str:
    return (handle or "").strip().lower()


def validate_handle(handle: str) -> str:
    normalized = normalize_handle(handle)
    if not HANDLE_RE.match(normalized):
        raise ValidationError(
            "Handles must be 3-32 characters of lowercase letters, digits, "
            "or underscores."
        )
    if normalized in RESERVED_HANDLES:
        raise ValidationError("That handle is reserved.")
    return normalized
```

- [ ] **Step 4: Implement the adapter and signup form**

Create `accounts/adapter.py`:

```python
from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return settings.INSTANCE_OPEN_REGISTRATIONS
```

Create `accounts/forms.py`:

```python
from allauth.account.forms import SignupForm as BaseSignupForm
from django import forms
from django.core.exceptions import ValidationError

from actors.handles import validate_handle
from actors.models import Actor
from actors.services import create_local_actor


class SignupForm(BaseSignupForm):
    handle = forms.CharField(
        max_length=32,
        label="Handle",
        help_text=(
            "Your fediverse username: @handle@this-instance. Lowercase letters, "
            "digits, and underscores."
        ),
    )

    def clean_handle(self):
        handle = validate_handle(self.cleaned_data["handle"])
        if Actor.objects.filter(handle=handle, domain="").exists():
            raise ValidationError("That handle is already taken.")
        return handle

    def save(self, request):
        user = super().save(request)
        create_local_actor(self.cleaned_data["handle"], user=user)
        return user
```

Modify `config/settings/base.py` — add to the auth block:

```python
ACCOUNT_ADAPTER = "accounts.adapter.AccountAdapter"
ACCOUNT_FORMS = {"signup": "accounts.forms.SignupForm"}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_handles.py tests/test_auth_signup.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add accounts/adapter.py accounts/forms.py actors/handles.py config/settings/base.py tests/test_handles.py tests/test_auth_signup.py
git commit -m "feat: gate signup and create the user actor from a chosen handle"
```

---

### Task 3: MFA — TOTP, WebAuthn passkeys, and recovery codes

**Files:**
- Modify: `config/settings/base.py`
- Create: `tests/test_auth_mfa.py`

**Interfaces:**
- Consumes: allauth MFA (Task 1), custom `User` (Ticket 2a).
- Produces: allauth MFA URL names (`mfa_index`, `mfa_activate_totp`, …); `MFA_SUPPORTED_TYPES` and passkey-login configuration.

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth_mfa.py`:

```python
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

PASSWORD = "a-strong-passphrase-42"


def test_mfa_types_and_passkey_login_are_configured():
    assert set(settings.MFA_SUPPORTED_TYPES) >= {"totp", "webauthn", "recovery_codes"}
    assert settings.MFA_PASSKEY_LOGIN_ENABLED is True


@pytest.mark.django_db
def test_mfa_index_requires_login(client):
    response = client.get(reverse("mfa_index"))
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


@pytest.mark.django_db
def test_mfa_index_renders_for_logged_in_user(client):
    user = get_user_model().objects.create_user(
        email="reader@example.com", password=PASSWORD
    )
    client.force_login(user)
    response = client.get(reverse("mfa_index"))
    assert response.status_code == 200
    assert b"Two-Factor" in response.content or b"two-factor" in response.content.lower()


@pytest.mark.django_db
def test_totp_activation_page_renders_for_logged_in_user(client):
    user = get_user_model().objects.create_user(
        email="reader@example.com", password=PASSWORD
    )
    client.force_login(user)
    response = client.get(reverse("mfa_activate_totp"))
    assert response.status_code == 200
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_auth_mfa.py -v`
Expected: FAIL — `MFA_SUPPORTED_TYPES` / `MFA_PASSKEY_LOGIN_ENABLED` are unset, and `mfa_index` is not configured.

- [ ] **Step 3: Configure MFA**

Modify `config/settings/base.py` — add to the auth block:

```python
MFA_SUPPORTED_TYPES = ["totp", "webauthn", "recovery_codes"]
MFA_PASSKEY_LOGIN_ENABLED = True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_auth_mfa.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add config/settings/base.py tests/test_auth_mfa.py
git commit -m "feat: enable TOTP, passkey login, and recovery codes"
```

---

### Task 4: Production HTTPS and cookie hardening

**Files:**
- Modify: `config/settings/prod.py`, `tests/test_prod_settings.py`

**Interfaces:**
- Consumes: `config.settings.prod` (previous tickets).
- Produces: production security settings that `manage.py check --deploy` recognizes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prod_settings.py`:

```python
def test_prod_settings_harden_transport_and_cookies(monkeypatch):
    settings = _reload_prod(monkeypatch, secret_key="s" * 50)
    assert settings.SECURE_SSL_REDIRECT is True
    assert settings.SESSION_COOKIE_SECURE is True
    assert settings.CSRF_COOKIE_SECURE is True
    assert settings.SECURE_HSTS_SECONDS >= 31536000
    assert settings.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
    assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_prod_settings.py::test_prod_settings_harden_transport_and_cookies -v`
Expected: FAIL — the attributes are unset.

- [ ] **Step 3: Add the hardening settings**

Modify `config/settings/prod.py` — add after the `FIELD_ENCRYPTION_KEY` validation:

```python
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_prod_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite, lint, and deploy check**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

Run (informational; may still warn about unrelated items): `DJANGO_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(64))") DJANGO_ALLOWED_HOSTS=panels.test FIELD_ENCRYPTION_KEY=MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY= INSTANCE_URL=https://panels.test uv run python manage.py check --deploy --settings=config.settings.prod`
Expected: no security errors (warnings acceptable).

- [ ] **Step 6: Commit**

```bash
git add config/settings/prod.py tests/test_prod_settings.py
git commit -m "feat: enforce secure cookies and HSTS in production"
```

---

## Self-Review

**Spec coverage (Ticket 2b scope):** email/password auth with mandatory verification (Task 1); registration gated by `INSTANCE_OPEN_REGISTRATIONS`, matching the NodeInfo flag (Task 2); user-chosen handle → Person actor at signup (Task 2); passwordless passkey login + TOTP + recovery codes, optional (Task 3); production transport/cookie hardening (Task 4). OAuth 2.1/OIDC is explicitly deferred.

**Placeholder scan:** no TBD/TODO steps; every code step carries full content.

**Type consistency:** `create_local_actor` (Ticket 2a) is the single actor-construction path; `validate_handle` is the single handle-validation path used by both the form and (transitively) the reserved-set tests; `_reload_prod` in `tests/test_prod_settings.py` is extended consistently across tickets.

**Known deviations recorded as ledger rulings during execution:** (1) `django.contrib.sites` is not required by allauth 65, so it is not added; (2) a full WebAuthn ceremony is not exercised headlessly — Task 3 tests configuration and page rendering, not the browser credential flow; (3) the user's `Actor` is created at signup (handle reserved) rather than after email confirmation, per the chosen ruling.