import logging

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.tasks import task
from PIL import Image

from media.derivatives import DERIVATIVE_SPECS, render_derivative
from media.models import Media, MediaDerivative, MediaStatus

logger = logging.getLogger(__name__)


def _replace_derivatives(media, generated):
    old_names = [derivative.file.name for derivative in media.derivatives.all()]
    new_names = []
    try:
        with transaction.atomic():
            media.derivatives.all().delete()
            for kind, buffer, width, height in generated:
                derivative = MediaDerivative(
                    media=media,
                    kind=kind,
                    width=width,
                    height=height,
                    bytes=buffer.getbuffer().nbytes,
                )
                derivative.file.save(
                    f"{kind}.webp", ContentFile(buffer.getvalue()), save=False
                )
                new_names.append(derivative.file.name)
                derivative.save()
    except Exception:
        for name in new_names:
            default_storage.delete(name)
        raise
    for name in old_names:
        default_storage.delete(name)


@task
def generate_derivatives(media_id):
    media = Media.objects.filter(pk=media_id).first()
    if media is None:
        return
    try:
        generated = []
        media.original.open("rb")
        with Image.open(media.original) as image:
            image.load()
            for kind in DERIVATIVE_SPECS:
                buffer, width, height = render_derivative(image, kind)
                generated.append((kind, buffer, width, height))
        _replace_derivatives(media, generated)
        media.status = MediaStatus.READY
        media.save(update_fields=["status", "updated_at"])
    except Exception as exc:
        logger.warning("Derivative generation failed for media %s: %s", media_id, exc)
        media.status = MediaStatus.FAILED
        media.save(update_fields=["status", "updated_at"])
