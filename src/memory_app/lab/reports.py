"""Only serve actual raster images explicitly referenced by a local report."""
import base64
import io
import warnings
from pathlib import Path
from fastapi import HTTPException
from PIL import Image
from memory_app.media.validation import MAX_IMAGE, decode_image
from memory_app.memory.errors import InvalidContent


def report_assets(value):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "image" and isinstance(item, str):
                result.add(item)
            elif key == "images" and isinstance(item, list):
                result.update(path for path in item if isinstance(path, str))
            result.update(report_assets(item))
    elif isinstance(value, list):
        for item in value:
            result.update(report_assets(item))
    return result


def read_report_image(directory, name):
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        raise HTTPException(404, "Invalid report image")
    directory = Path(directory).resolve()
    target = (directory / name).resolve()
    if not target.is_relative_to(directory) or not target.is_file():
        raise HTTPException(404, "Report image unavailable")
    if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or target.stat().st_size > MAX_IMAGE:
        raise HTTPException(404, "Unsupported report image")
    with target.open("rb") as stream:
        raw = stream.read(MAX_IMAGE + 1)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
        if mime is None:
            raise ValueError("Unsupported raster")
        decode_image(f"data:{mime};base64," + base64.b64encode(raw).decode())
    except (OSError, ValueError, InvalidContent, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(404, "Invalid report image") from exc
    return raw, mime
