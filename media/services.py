import hashlib
import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from PIL import Image, UnidentifiedImageError

from comics import permissions
from comics.models import Page
from media.models import Media, MediaStatus

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
FORMAT_TO_EXTENSION = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}


def _validate_upload(uploaded_file):
    max_bytes = getattr(settings, "MEDIA_MAX_UPLOAD_BYTES", 25_000_000)
    allowed = set(
        getattr(settings, "MEDIA_ALLOWED_CONTENT_TYPES", DEFAULT_ALLOWED_CONTENT_TYPES)
    )
    if uploaded_file.size > max_bytes:
        raise ValidationError({"file": "That image is too large."})
    if getattr(uploaded_file, "content_type", None) not in allowed:
        raise ValidationError({"file": "Unsupported image type."})
    try:
        with Image.open(uploaded_file) as image:
            image_format = (image.format or "").upper()
            image.verify()
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            width, height = image.size
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValidationError({"file": "That file is not a valid image."}) from exc
    finally:
        uploaded_file.seek(0)
    if image_format not in FORMAT_TO_MIME:
        raise ValidationError({"file": "Unsupported image type."})
    return width, height, image_format


def _sha256(uploaded_file):
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


@transaction.atomic
def add_media(user, page, uploaded_file, *, alt_text=""):
    if not permissions.can_author(user, page.series.comic):
        raise PermissionDenied("You cannot add media to this comic.")
    width, height, image_format = _validate_upload(uploaded_file)
    digest = _sha256(uploaded_file)
    locked_page = Page.objects.select_for_update().get(pk=page.pk)
    current = locked_page.media.aggregate(Max("position"))["position__max"]
    media = Media(
        page=locked_page,
        position=(current or 0) + 1,
        alt_text=alt_text,
        content_type=FORMAT_TO_MIME[image_format],
        width=width,
        height=height,
        bytes=uploaded_file.size,
        sha256=digest,
        status=MediaStatus.PENDING,
    )
    media.original.save(
        f"original{FORMAT_TO_EXTENSION[image_format]}", uploaded_file, save=False
    )
    try:
        media.save()
    except Exception:
        media.original.delete(save=False)
        raise
    from media.tasks import generate_derivatives

    generate_derivatives.enqueue(str(media.id))
    return media


@transaction.atomic
def remove_media(user, media):
    if not permissions.can_author(user, media.page.series.comic):
        raise PermissionDenied("You cannot remove media from this comic.")
    Page.objects.select_for_update().get(pk=media.page_id)
    names = [media.original.name]
    names += [derivative.file.name for derivative in media.derivatives.all()]
    media.delete()
    for name in names:
        try:
            default_storage.delete(name)
        except Exception:
            logger.warning("Failed to delete media file %s", name)
