"""Transient query image understanding: never commits memories or media."""
from memory_app.vision.pipeline import analyze_cached


def describe_query_images(store, media, vision, user_id, parts):
    from memory_app.memory.parser import parse_content

    content, text, keys, images = parse_content(parts, user_id, media)
    artifacts = []
    if vision and images:
        cache = {}
        mimes = {p["media_key"]: p["mime"] for p in content if p["type"] == "image_url"}
        for key, raw in images:
            value = analyze_cached(store, vision, user_id, key, raw, mimes[key], cache)
            artifacts.append({"media_key": key, **value})
    derived = [value[field] for value in artifacts for field in ("description", "visible_text") if value[field]]
    return "\n".join([text, *derived]).strip(), keys, artifacts
