import json
from pathlib import Path

from django.core.files.storage import default_storage
from django.core.management.base import CommandError


def comic_payload(comic, media_names):
    tags = list(comic.tags.order_by("name").values_list("name", flat=True))
    series_data = []
    for series in comic.series.order_by("position", "id"):
        pages = []
        for page in series.pages.order_by("position", "id"):
            media = []
            for item in page.media.order_by("position", "id"):
                media_names.append(item.original.name)
                media.append(
                    {
                        "id": str(item.id),
                        "file": item.original.name,
                        "alt_text": item.alt_text,
                        "content_type": item.content_type,
                        "width": item.width,
                        "height": item.height,
                        "bytes": item.bytes,
                        "sha256": item.sha256,
                    }
                )
            pages.append(
                {
                    "id": str(page.id),
                    "position": page.position,
                    "title": page.title,
                    "status": page.status,
                    "published_at": page.published_at,
                    "ap_id": page.ap_id,
                    "transcript": page.transcript,
                    "author_commentary": page.author_commentary,
                    "sensitive": page.sensitive,
                    "media": media,
                }
            )
        series_data.append({"title": series.title, "slug": series.slug, "pages": pages})
    return {
        "slug": comic.slug,
        "title": comic.title,
        "description": comic.description,
        "content_rating": comic.content_rating,
        "tags": tags,
        "payment_links": comic.payment_links,
        "actor": {
            "ap_id": comic.actor.ap_id,
            "handle": comic.actor.handle,
        },
        "series": series_data,
    }


def copy_media(names, media_dir):
    root = Path(media_dir)
    for name in names:
        target = (root / name).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise CommandError(f"Unsafe media path: {name}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        with default_storage.open(name, "rb") as source:
            target.write_bytes(source.read())


def write_payload(data, output):
    payload = json.dumps(data, indent=2, default=str)
    if output:
        Path(output).write_text(payload, encoding="utf-8")
        return None
    return payload
