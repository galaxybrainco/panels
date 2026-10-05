import hashlib
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

DEFAULT_COLOR = (180, 20, 20)


def image_bytes(size=(40, 60), image_format="PNG", color=DEFAULT_COLOR):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
    return buffer.getvalue()


def upload(
    name="panel.png", size=(40, 60), image_format="PNG", content_type="image/png"
):
    data = image_bytes(size=size, image_format=image_format)
    return SimpleUploadedFile(name, data, content_type=content_type)


def make_ready_media(page, *, position=1, alt_text="A panel"):
    from media.models import Media, MediaStatus

    data = image_bytes()
    return Media.objects.create(
        page=page,
        position=position,
        alt_text=alt_text,
        original=f"media/{page.id}/{position}.png",
        content_type="image/png",
        width=40,
        height=60,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        status=MediaStatus.READY,
    )


def corrupted_png(index=50):
    data = bytearray(image_bytes())
    data[index] ^= 0xFF
    return SimpleUploadedFile("corrupt.png", bytes(data), content_type="image/png")


def spoofed_upload(name="spoof.png", image_format="BMP", content_type="image/png"):
    data = image_bytes(image_format=image_format)
    return SimpleUploadedFile(name, data, content_type=content_type)
