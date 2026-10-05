from io import BytesIO

from PIL import Image

from media.derivatives import render_derivative
from media.models import DerivativeKind


def _image(size, mode="RGB"):
    buffer = BytesIO()
    Image.new(mode, size, (120, 30, 30) if mode == "RGB" else 128).save(
        buffer, format="PNG"
    )
    buffer.seek(0)
    image = Image.open(buffer)
    image.load()
    return image


def test_thumbnail_caps_longest_side():
    buffer, width, height = render_derivative(
        _image((1200, 800)), DerivativeKind.THUMBNAIL
    )
    assert (width, height) == (400, round(800 * 400 / 1200))
    assert Image.open(buffer).format == "WEBP"


def test_display_caps_width():
    buffer, width, height = render_derivative(
        _image((3000, 1000)), DerivativeKind.DISPLAY
    )
    assert (width, height) == (1600, round(1000 * 1600 / 3000))


def test_federation_caps_longest_side():
    _, width, height = render_derivative(
        _image((5000, 3000)), DerivativeKind.FEDERATION
    )
    assert max(width, height) == 4096


def test_small_images_are_not_upscaled():
    _, width, height = render_derivative(_image((100, 80)), DerivativeKind.THUMBNAIL)
    assert (width, height) == (100, 80)


def test_exif_orientation_is_applied():
    buffer = BytesIO()
    image = Image.new("RGB", (400, 200), (5, 5, 5))
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 CW on display
    image.save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)
    oriented = Image.open(buffer)
    oriented.load()
    _, width, height = render_derivative(oriented, DerivativeKind.THUMBNAIL)
    assert (width, height) == (200, 400)
