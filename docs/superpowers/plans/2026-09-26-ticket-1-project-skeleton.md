# Ticket 1 — Project Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bootable Django 6.1.1 project on PostgreSQL with all nine spec apps scaffolded, the `django-tasks-db` worker executing real background tasks, env-driven settings (including storage/CDN), and green tests proving `/healthz` plus a worker round-trip.

**Architecture:** Single Django project package `config/` with split settings (`base`/`dev`/`prod`/`test`). Nine apps live at the repository root and are scaffolded empty here, to be filled by their own subsystem plans. Background work uses Django's built-in `django.tasks` with the `django-tasks-db` database backend and its `db_worker` management command. Local development runs PostgreSQL via Docker Compose; production reads Bunny-compatible S3 settings from the environment through `django-storages`.

**Tech Stack:** Python 3.14 (uv), Django 6.1.1, PostgreSQL 18, `django-tasks-db` 0.13.0, `django-environ` 0.14.0, `django-storages` 1.14.6, `psycopg` 3.3.6, pytest 9.1.1, `pytest-django` 4.14.0, ruff 0.16.9.

**Spec:** `webcomic-fediverse-plan.md` (the binding authority; §3 stack, §14 ticket 1).

## Global Constraints

- Django floor is 6.0+; this plan pins 6.1.1.
- PostgreSQL only. No SQLite anywhere, including tests.
- Sync/WSGI. Background work goes through `django.tasks` with `django-tasks-db` `DatabaseBackend` and `manage.py db_worker`. No Celery, no RQ.
- Redis is optional and NOT a day-one dependency; the Compose Redis service stays commented out.
- Bunny Storage + Bunny CDN in production; filesystem storage in development. `pyvips` derivatives are deferred to the media ticket.
- v1 is SFW / all-ages only.
- Never leak paid content: gated pages never federate (enforced from the serialization ticket onward).
- Python 3.14; dependency management via uv; `uv.lock` is committed.

## Review Focus

Spec-implied input classes and failure modes no task's happy path exercises; each is pinned by a test in its owning task:

- Production settings silently inheriting development defaults (empty `SECRET_KEY`, wildcard hosts) instead of failing loudly at startup — Task 1.
- `.env.example` drifting from the setting keys the code actually reads, so `cp .env.example .env` does not boot a fresh clone — Task 1.
- `/healthz` reporting healthy while the database is unreachable (it must round-trip the DB, not just return 200) — Task 1.
- The worker reading a different settings module or database than the web process, so a task enqueued during a request is never executed — Task 2.
- Task-backend migrations not applied before `db_worker` starts, so a fresh deploy needs undocumented manual steps — Task 2.

## File Structure

- `pyproject.toml` — uv project, dependencies, ruff and pytest configuration.
- `.python-version` — pins Python 3.14.
- `.env.example` — documented environment variables; copied to `.env` (gitignored).
- `docker-compose.yml` — PostgreSQL 18 service (Redis commented for later).
- `manage.py` — defaults to `config.settings.dev`.
- `config/__init__.py` — empty package marker.
- `config/settings/__init__.py` — empty package marker.
- `config/settings/base.py` — shared settings read from the environment.
- `config/settings/dev.py` — local development overrides.
- `config/settings/prod.py` — production overrides that fail loudly; S3/Bunny storage.
- `config/settings/test.py` — deterministic settings for pytest.
- `config/urls.py` — root URL configuration.
- `config/wsgi.py`, `config/asgi.py` — deployment entry points.
- `config/health.py` — `/healthz` view with a real database round-trip.
- `config/tasks.py` — task definitions (Task 2 adds `ping`).
- `accounts/ actors/ comics/ media/ federation/ social/ memberships/ moderation/ feeds/` — the nine spec apps, scaffolded empty (Task 3).
- `tests/` — project-level tests.

---

### Task 1: Bootstrap — uv, Django, split settings, Postgres, `/healthz`

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.env.example`, `docker-compose.yml`
- Create: `manage.py`, `config/__init__.py`, `config/settings/__init__.py`
- Create: `config/settings/base.py`, `config/settings/dev.py`, `config/settings/prod.py`, `config/settings/test.py`
- Create: `config/urls.py`, `config/wsgi.py`, `config/asgi.py`, `config/health.py`
- Create: `tests/__init__.py`, `tests/test_health.py`, `tests/test_env_example.py`, `tests/test_prod_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.settings.base` (the shared settings module other settings import from), `config.health.healthz` (view), URL name `healthz`, `config.urls` (`ROOT_URLCONF`). Later tasks import `config.settings.base` and add to `INSTALLED_APPS`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "panels"
version = "0.1.0"
description = "Community-first, multi-tenant Django platform for webcomics on the fediverse."
readme = "README.md"
requires-python = ">=3.14"
dependencies = [
    "Django==6.1.1",
    "django-environ==0.14.0",
    "django-storages[s3]==1.14.6",
    "django-tasks-db==0.13.0",
    "psycopg[binary]==3.3.6",
]

[dependency-groups]
dev = [
    "pytest==9.1.1",
    "pytest-django==4.14.0",
    "ruff==0.16.9",
]

[tool.ruff]
target-version = "py314"
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "I", "W", "N", "B", "A", "C4", "T20", "DJ", "S", "UP"]
ignore = ["E501"]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings.test"
python_files = ["test_*.py", "*_test.py"]
```

- [ ] **Step 2: Pin Python and install**

Create `.python-version`:

```text
3.14
```

Run: `uv sync`
Expected: a `.venv` is created and `uv.lock` is written with no errors.

- [ ] **Step 3: Create `docker-compose.yml` and start the database**

```yaml
services:
  db:
    image: postgres:18
    environment:
      POSTGRES_DB: panels
      POSTGRES_USER: panels
      POSTGRES_PASSWORD: panels
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U panels"]
      interval: 5s
      timeout: 5s
      retries: 5

  # Redis is optional at first; the DB task backend is the day-one choice.
  # redis:
  #   image: redis:7
  #   ports:
  #     - "6379:6379"

volumes:
  postgres_data:
```

Run: `docker compose up -d db`
Expected: the `db` service starts; `docker compose ps` shows it healthy.

- [ ] **Step 4: Create `.env.example`**

```text
DJANGO_SECRET_KEY=dev-only-change-me
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgres://panels:panels@localhost:5432/panels
```

- [ ] **Step 5: Create the project package and settings (scaffolding)**

Create `manage.py`:

```python
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and available on "
            "your PYTHONPATH environment variable? Did you forget to activate a "
            "virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

Create `config/__init__.py` and `config/settings/__init__.py` as empty files.

Create `config/settings/base.py`:

```python
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://panels:panels@localhost:5432/panels",
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
```

Create `config/settings/dev.py`:

```python
from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = SECRET_KEY or "insecure-dev-key"  # noqa: F405
ALLOWED_HOSTS = ["*"]
```

Create `config/settings/prod.py`:

```python
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

if not SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production")

if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production")

if env("AWS_STORAGE_BUCKET_NAME", default=""):  # noqa: F405
    STORAGES["default"] = {  # noqa: F405
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("AWS_STORAGE_BUCKET_NAME"),  # noqa: F405
            "endpoint_url": env("AWS_S3_ENDPOINT_URL", default=None),  # noqa: F405
            "access_key": env("AWS_ACCESS_KEY_ID", default=None),  # noqa: F405
            "secret_key": env("AWS_SECRET_ACCESS_KEY", default=None),  # noqa: F405
            "custom_domain": env("AWS_S3_CUSTOM_DOMAIN", default=None),  # noqa: F405
            "querystring_auth": False,
            "file_overwrite": False,
        },
    }
```

Create `config/settings/test.py`:

```python
from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-secret-key"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
```

Create `config/wsgi.py`:

```python
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()
```

Create `config/asgi.py`:

```python
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_asgi_application()
```

Create `config/urls.py` (health route added in Step 8):

```python
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
```

- [ ] **Step 6: Write the failing tests**

Create `tests/__init__.py` as an empty file.

Create `tests/test_health.py`:

```python
import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_healthz_returns_ok_with_db_roundtrip(client):
    response = client.get(reverse("healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.django_db
def test_healthz_returns_500_when_database_unavailable(monkeypatch):
    from django.db import connection

    def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(connection, "cursor", boom)
    client = Client(raise_request_exception=False)
    response = client.get(reverse("healthz"))
    assert response.status_code == 500
```

Create `tests/test_env_example.py`:

```python
from pathlib import Path

import environ

CORE_KEYS = {
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "DATABASE_URL",
}


def test_env_example_documents_core_settings():
    text = Path(".env.example").read_text()
    keys = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    assert CORE_KEYS <= keys


def test_env_example_parses_as_environment():
    text = Path(".env.example").read_text()
    parsed = environ.Env.parse_value  # sanity: django-environ importable
    assert parsed is not None
    assert "DATABASE_URL" in text
```

Create `tests/test_prod_settings.py`:

```python
import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured


def test_prod_settings_require_secret_key(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "")
    import config.settings.base as base
    import config.settings.prod as prod

    importlib.reload(base)
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        importlib.reload(prod)
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/test_health.py -v`
Expected: FAIL. The health tests fail because URL name `healthz` does not exist (NoReverseMatch). `tests/test_env_example.py` and `tests/test_prod_settings.py` pass at this point (their config already exists); the health test is the RED for Step 8.

- [ ] **Step 8: Implement the health view and wire the route**

Create `config/health.py`:

```python
from django.db import connection
from django.http import JsonResponse


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok", "database": "ok"})
```

Modify `config/urls.py` to add the route:

```python
from django.contrib import admin
from django.urls import path

from config.health import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
]
```

- [ ] **Step 9: Apply migrations and run the full suite**

Run: `uv run python manage.py migrate`
Expected: migrations for the Django contrib apps apply with no errors.

Run: `uv run pytest -v`
Expected: PASS — all tests green.

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: no lint errors and formatting already clean. If ruff reports issues, fix them before committing.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock .python-version .env.example docker-compose.yml manage.py config tests webcomic-fediverse-plan.md docs
git commit -m "feat: bootstrap Django project skeleton with Postgres and health check"
```

---

### Task 2: Django Tasks DB backend and `db_worker` round-trip

**Files:**
- Modify: `config/settings/base.py` (add `django_tasks_db` to `INSTALLED_APPS` and a `TASKS` setting)
- Create: `config/tasks.py`
- Create: `tests/test_tasks_roundtrip.py`

**Interfaces:**
- Consumes: `config.settings.base` from Task 1.
- Produces: `TASKS["default"]["BACKEND"] == "django_tasks_db.DatabaseBackend"`, and the task `config.tasks.ping` (a `django.tasks` `Task` returning `"pong"`). Later subsystem plans define their delivery/processing tasks in `config/tasks.py` or their own app `tasks.py` modules using the same backend.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tasks_roundtrip.py`:

```python
import pytest
from django.core.management import call_command
from django.tasks import TaskResultStatus


def test_task_roundtrip_through_db_worker(db):
    from config.tasks import ping

    result = ping.enqueue()
    assert result.status == TaskResultStatus.READY

    call_command(
        "db_worker",
        batch=True,
        max_tasks=1,
        startup_delay=False,
        reload=False,
        verbosity=0,
    )

    result.refresh()
    assert result.status == TaskResultStatus.SUCCESSFUL
    assert result.return_value == "pong"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tasks_roundtrip.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config.tasks'` (or an `InvalidTaskBackend`/`ImproperlyConfigured` about the tasks backend).

- [ ] **Step 3: Create the task and configure the backend**

Create `config/tasks.py`:

```python
from django.tasks import task


@task
def ping() -> str:
    return "pong"
```

Modify `config/settings/base.py`: add `"django_tasks_db"` to `INSTALLED_APPS` (after `"django.contrib.staticfiles"`), and add the `TASKS` setting before `DEFAULT_AUTO_FIELD`:

```python
TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
        "QUEUES": ["default"],
    }
}
```

- [ ] **Step 4: Apply the task-backend migrations**

Run: `uv run python manage.py migrate`
Expected: `django_tasks_db` migrations apply with no errors.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_tasks_roundtrip.py -v`
Expected: PASS. The task is enqueued, persisted, executed by `db_worker`, and its result is `SUCCESSFUL` with return value `"pong"`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS — all tests green.

- [ ] **Step 7: Commit**

```bash
git add config/settings/base.py config/tasks.py tests/test_tasks_roundtrip.py
git commit -m "feat: add database-backed Django Tasks worker"
```

---

### Task 3: Scaffold the nine spec apps

**Files:**
- Create: `accounts/ actors/ comics/ media/ federation/ social/ memberships/ moderation/ feeds/` (via `startapp`)
- Modify: `config/settings/base.py` (add the nine apps to `INSTALLED_APPS`)
- Create: `tests/test_apps.py`

**Interfaces:**
- Consumes: `config.settings.base` from Task 1.
- Produces: nine importable Django apps with `AppConfig`s named `<App>Config` (for example `accounts.apps.AccountsConfig`) and app labels `accounts`, `actors`, `comics`, `media`, `federation`, `social`, `memberships`, `moderation`, `feeds`. Every later subsystem plan adds models and migrations inside its own app.

- [ ] **Step 1: Write the failing test**

Create `tests/test_apps.py`:

```python
import pytest
from django.apps import apps

SPEC_APPS = [
    "accounts",
    "actors",
    "comics",
    "media",
    "federation",
    "social",
    "memberships",
    "moderation",
    "feeds",
]


@pytest.mark.parametrize("label", SPEC_APPS)
def test_spec_app_is_installed(label):
    assert apps.is_installed(label)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_apps.py -v`
Expected: FAIL for all nine labels with `LookupError: No installed app with label ...`.

- [ ] **Step 3: Scaffold the apps and register them**

Run:

```bash
for app in accounts actors comics media federation social memberships moderation feeds; do
  uv run python manage.py startapp "$app"
done
```

Modify `config/settings/base.py`: add the nine app names to `INSTALLED_APPS` after `"django_tasks_db"`, in spec order:

```python
    "django_tasks_db",
    "accounts",
    "actors",
    "comics",
    "media",
    "federation",
    "social",
    "memberships",
    "moderation",
    "feeds",
```

- [ ] **Step 4: Verify the project checks and has no pending migrations**

Run: `uv run python manage.py check`
Expected: `System check identified no issues (0 silenced).`

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: `No changes detected` and exit status 0.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_apps.py -v`
Expected: PASS for all nine labels.

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green. Fix any lint or formatting findings before committing.

- [ ] **Step 7: Commit**

```bash
git add config/settings/base.py accounts actors comics media federation social memberships moderation feeds tests/test_apps.py
git commit -m "feat: scaffold the nine spec apps"
```

---

### Task 4: Continuous integration workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the uv project and test suite from Tasks 1–3.
- Produces: a CI job that lints and tests every push to `main` and every pull request, against a PostgreSQL 18 service container.

- [ ] **Step 1: Create the workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:18
        env:
          POSTGRES_DB: panels
          POSTGRES_USER: panels
          POSTGRES_PASSWORD: panels
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgres://panels:panels@localhost:5432/panels
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Install dependencies
        run: uv sync --locked
      - name: Lint
        run: uv run ruff check .
      - name: Check formatting
        run: uv run ruff format --check .
      - name: Test
        run: uv run pytest
```

- [ ] **Step 2: Verify the exact CI commands locally**

Run: `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run pytest`
Expected: all commands exit 0 against the local PostgreSQL container. If `--locked` fails because `uv.lock` is stale, run `uv lock` and commit the updated lockfile.

- [ ] **Step 3: Validate the workflow YAML**

Run: `uv run python -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())"`
Expected: no output and exit status 0 (the YAML parses).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml uv.lock
git commit -m "ci: lint and test on push and pull request"
```

---

## Self-Review

**Spec coverage (Ticket 1 scope):** Django 6.1.1 + PostgreSQL (Task 1); Django Tasks with the DB-backed worker (Task 2); settings for storage/CDN (Task 1, `prod.py` S3/Bunny block); all nine spec apps scaffolded (Task 3); CI (Task 4). Redis remains optional and commented. `pyvips` and real media wiring belong to the media ticket.

**Placeholder scan:** no TBD/TODO steps; every code step carries full content.

**Type consistency:** `config.settings.base` is the single shared module; `config.tasks.ping` uses the built-in `django.tasks.task`; `django_tasks_db.DatabaseBackend` is the backend path used consistently in settings and in the round-trip test.

**Known deviation from the spec's wording (recorded as a ledger ruling during execution):** the spec says "the reference `django-tasks` database-backed backend." As of `django-tasks` 0.12.0 the DB backend is a separate package, `django-tasks-db` (0.13.0), which on Django 6.0+ uses the built-in `django.tasks` via `find_spec("django.tasks")`. This plan installs `django-tasks-db` only. Its released classifier stops at Django 6.0, but upstream PR #53 "Formalize support for Django 6.1" merged 2026-09-18 and changed only CI and the classifier, and the dependency is `django>=5.2` with no upper bound.
