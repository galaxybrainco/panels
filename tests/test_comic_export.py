import json
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from comics.services import create_comic, create_page, create_series
from media.services import add_media
from tests.media_support import upload
from tests.test_reader import _user


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.django_db
def test_export_comic_writes_json_and_media(tmp_path):
    owner = _user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Export Comic")
    series = create_series(owner, comic, "Main")
    page = create_page(owner, series, title="Page")
    media = add_media(owner, page, upload(), alt_text="Panel")
    output = tmp_path / "comic.json"
    media_dir = tmp_path / "media"
    call_command(
        "export_comic",
        comic.slug,
        output=str(output),
        media_dir=str(media_dir),
    )
    data = json.loads(output.read_text())
    assert data["comic"]["slug"] == comic.slug
    assert data["comic"]["title"] == "Export Comic"
    assert data["comic"]["series"][0]["pages"][0]["media"][0]["file"] == (
        media.original.name
    )
    assert (media_dir / media.original.name).exists()


@pytest.mark.django_db
def test_export_comic_requires_an_existing_comic(tmp_path):
    with pytest.raises(CommandError):
        call_command("export_comic", "missing", output=str(tmp_path / "missing.json"))
