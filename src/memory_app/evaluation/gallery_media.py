import base64
import io
import warnings
import hashlib
from pathlib import Path
from PIL import Image, ImageOps, __version__ as pillow_version
from memory_app.media.validation import decode_image, MAX_IMAGE

MAX_ORIGINAL = 64 * 1024 * 1024
COMPRESSION_POLICY = "oversize-jpeg-v1:pillow-" + pillow_version


def validate_original(raw):
    """Dataset originals have a separate bounded download limit, not an API limit."""
    if not raw or len(raw) > MAX_ORIGINAL:
        raise ValueError("Dataset original exceeds 64 MiB download limit")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Unsupported dataset image format")
            image.verify()


def prepared_image_uri(path, *, compress_oversize=False):
    """Keep original bytes on disk; derive only oversized images with explicit consent."""
    path = Path(path)
    if path.stat().st_size > MAX_ORIGINAL:
        raise ValueError("Dataset original exceeds 64 MiB download limit")
    if path.stat().st_size <= MAX_IMAGE:
        uri, raw = image_uri(path)
        return uri, raw, {"transformed": False, "source_sha256": hashlib.sha256(raw).hexdigest(),
                          "source_bytes": len(raw), "output_bytes": len(raw), "policy": "original-v1"}
    if not compress_oversize:
        raise ValueError("Oversized dataset image requires explicit --compress-oversize")
    raw = path.read_bytes()
    validate_original(raw)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as original:
            if getattr(original, "n_frames", 1) != 1 or original.width * original.height > 60_000_000:
                raise ValueError("Oversized image is animated or exceeds safe pixel limit")
            size = original.size
            oriented = ImageOps.exif_transpose(original)
            if oriented.mode == "RGB":
                image = oriented  # Avoid another full-size RGB/RGBA copy for photographs.
            elif "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = oriented.convert("RGB")
    for scale in range(9):
        for quality in (95, 90, 85, 80, 70, 60):
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=False, subsampling=0)
            output = buffer.getvalue()
            if len(output) <= MAX_IMAGE:
                uri = "data:image/jpeg;base64," + base64.b64encode(output).decode()
                decode_image(uri)
                return uri, output, {"transformed": True, "lossy": True, "policy": COMPRESSION_POLICY,
                    "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
                    "source_dimensions": list(size), "output_dimensions": list(image.size),
                    "output_bytes": len(output), "output_sha256": hashlib.sha256(output).hexdigest(),
                    "jpeg_quality": quality, "exif_orientation_applied": True, "alpha_background": "white"}
        image = image.resize((max(1, int(image.width * .8)), max(1, int(image.height * .8))), Image.Resampling.LANCZOS)
    raise ValueError("Cannot prepare image within API byte budget")


def image_reference(dialog_path, reference):
    """Dataset ../image paths may only resolve inside the sibling image tree."""
    if not isinstance(reference, str) or not reference or ":" in reference or "\\" in reference:
        raise ValueError("Invalid dataset image reference")
    dialog_path = Path(dialog_path).resolve()
    root = (dialog_path.parent.parent / "image").resolve()
    target = (dialog_path.parent / reference).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Dataset image reference escapes image directory")
    return target, target.relative_to(root.parent).as_posix()


def image_uri(path):
    path = Path(path)
    if path.stat().st_size > MAX_IMAGE:
        raise ValueError(f"Unsupported or oversized image: {path.name}")
    raw = path.read_bytes()
    # Some upstream .jpg files actually contain PNG bytes. Preserve originals,
    # but declare their true MIME; do not relax the API's MIME consistency check.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
    if mime is None:
        raise ValueError(f"Unsupported image encoding: {path.name}")
    uri = f"data:{mime};base64," + base64.b64encode(raw).decode()
    decode_image(uri)
    return uri, raw
