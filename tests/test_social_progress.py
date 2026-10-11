from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from comics.services import create_comic, create_page, create_series
from social.progress import record_progress, resume_point
from tests.media_support import make_media


def _page(owner, handle):
    comic = create_comic(owner, handle, "Lunar Baboon")
    series = create_series(owner, comic, "Main")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    return series, page


def _user(prefix):
    return get_user_model().objects.create_user(
        email=f"{prefix}-{uuid4().hex[:8]}@example.com", password="x"
    )


@pytest.mark.django_db
def test_record_progress_upserts_last_page():
    owner = _user("o")
    reader = _user("r")
    series, first = _page(owner, f"comic{uuid4().hex[:6]}")
    second = create_page(owner, series)
    record_progress(reader, first)
    record_progress(reader, second)
    assert resume_point(reader, series).page == second
    from social.models import ReadingProgress

    assert ReadingProgress.objects.filter(user=reader, series=series).count() == 1


@pytest.mark.django_db
def test_resume_point_is_none_without_progress():
    owner = _user("o")
    reader = _user("r")
    series, _ = _page(owner, f"comic{uuid4().hex[:6]}")
    assert resume_point(reader, series) is None
