from io import BytesIO

from PIL import Image, ImageOps

from media.models import DerivativeKind

DERIVATIVE_SPECS = {
    DerivativeKind.THUMBNAIL: {"max_longest_side": 400, "quality": 80},
    DerivativeKind.DISPLAY: {"max_width": 1600, "quality": 82},
    DerivativeKind.FEDERATION: {"max_longest_side": 4096, "quality": 85},
}


def _prepare(image):
    transposed = ImageOps.exif_transpose(image)
    image = transposed if transposed is not None else image
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    elif image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    return image


def _fit_longest(image, longest):
    if max(image.width, image.height) <= longest:
        return image
    scale = longest / max(image.width, image.height)
    return image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )


def _fit_width(image, max_width):
    if image.width <= max_width:
        return image
    scale = max_width / image.width
    return image.resize(
        (max_width, round(image.height * scale)), Image.Resampling.LANCZOS
    )


def render_derivative(image, kind):
    spec = DERIVATIVE_SPECS[kind]
    prepared = _prepare(image)
    if "max_width" in spec:
        rendered = _fit_width(prepared, spec["max_width"])
    else:
        rendered = _fit_longest(prepared, spec["max_longest_side"])
    buffer = BytesIO()
    rendered.save(buffer, format="WEBP", quality=spec["quality"])
    buffer.seek(0)
    return buffer, rendered.width, rendered.height
