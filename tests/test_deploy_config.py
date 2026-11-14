from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _read(name):
    return (BASE / name).read_text()


def test_dockerfile_builds_and_runs_gunicorn():
    text = _read("Dockerfile")
    assert "uv sync --locked --no-dev" in text
    assert "collectstatic --noinput" in text
    assert "gunicorn" in text and "config.wsgi:application" in text


def test_dockerignore_excludes_local_state():
    ignored = _read(".dockerignore")
    for entry in (".venv", ".git", ".env", ".worktrees", "uploads"):
        assert entry in ignored


def test_fly_configs_define_apps_worker_and_health_checks():
    for env, app in (
        ("staging", "panels-staging"),
        ("production", "panels-production"),
    ):
        text = _read(f"fly.{env}.toml")
        assert f'app = "{app}"' in text
        assert "release_command" in text
        assert "createcachetable" in text
        assert 'worker = "python manage.py db_worker"' in text
        assert 'path = "/healthz"' in text
        assert f"https://{app}.fly.dev" in text
        assert 'DJANGO_SETTINGS_MODULE = "config.settings.prod"' in text


def test_deploy_workflow_auto_stages_and_gates_production():
    text = _read(".github/workflows/deploy.yml")
    assert "environment: staging" in text
    assert "environment: production" in text
    assert "flyctl deploy --config fly.staging.toml" in text
    assert "flyctl deploy --config fly.production.toml" in text
    assert "refs/heads/main" in text
    assert "refs/tags/v" in text


def test_production_settings_serve_static_and_exempt_healthz():
    text = _read("config/settings/prod.py")
    assert "WhiteNoiseMiddleware" in text
    assert "CompressedStaticFilesStorage" in text
    assert "SECURE_REDIRECT_EXEMPT" in text


def test_bootstrap_script_covers_both_environments():
    text = _read("scripts/fly_bootstrap.sh")
    assert "panels-staging" in text
    assert "panels-production" in text
    assert "flyctl postgres attach" in text
    assert "flyctl secrets set" in text
