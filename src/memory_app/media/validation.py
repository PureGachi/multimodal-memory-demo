import base64
import binascii
import io
import re
import warnings
from PIL import Image
from memory_app.memory.errors import InvalidContent

MAX_IMAGE = 10 * 1024 * 1024
MAX_MEDIA = 30 * 1024 * 1024
DATA_URI = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\r\n]+)$")


def decode_image(uri: str) -> tuple[str, bytes]:
    match = DATA_URI.fullmatch(uri)
    if not match or len(match[2]) > (MAX_IMAGE * 4 // 3 + 4096):
        raise InvalidContent("Expected inline PNG/JPEG/WebP, at most 10 MiB")
    try:
        raw = base64.b64decode(match[2].replace("\r", "").replace("\n", ""), validate=True)
        if len(raw) > MAX_IMAGE:
            raise ValueError("image exceeds 10 MiB")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                detected = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
                if detected != match[1]:
                    raise ValueError("image MIME does not match bytes")
                image.verify()
    except (ValueError, binascii.Error, OSError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidContent("Invalid image bytes or image size") from exc
    return match[1], raw
