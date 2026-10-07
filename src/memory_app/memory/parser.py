from memory_app.media.validation import MAX_MEDIA, decode_image
from memory_app.memory.errors import InvalidContent


def parse_content(content, user_id, media):
    """Return ordered stored content, index text, image keys and decoded images."""
    if isinstance(content, str):
        if not content.strip():
            raise InvalidContent("Content must not be blank")
        return content, content, [], []
    stored, texts, keys, images = [], [], [], []
    total = 0
    for part in content:
        if part["type"] == "text":
            if not part["text"].strip():
                raise InvalidContent("Text part must not be blank")
            stored.append(part)
            texts.append(part["text"])
        else:
            mime, raw = decode_image(part["image_url"]["url"])
            total += len(raw)
            if total > MAX_MEDIA:
                raise InvalidContent("Images exceed 30 MiB")
            key = media.key(user_id, raw)
            stored.append({"type": "image_url", "media_key": key, "mime": mime})
            keys.append(key)
            images.append((key, raw))
    return stored, "\n".join(texts), keys, images
