import logging
from uuid import uuid4

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def _check_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return "ok"


def _check_cache():
    key = f"healthz:{uuid4()}"
    cache.set(key, "ok", timeout=5)
    value = cache.get(key)
    cache.delete(key)
    if value != "ok":
        raise RuntimeError("cache round-trip failed")
    return "ok"


def healthz(request):
    checks = {}
    for name, check in (("database", _check_database), ("cache", _check_cache)):
        try:
            checks[name] = check()
        except Exception:
            logger.exception("Health check failed: %s", name)
            checks[name] = "error"
    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", **checks},
        status=200 if healthy else 503,
    )
