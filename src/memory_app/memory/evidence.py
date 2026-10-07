from memory_app.media.validation import MAX_MEDIA


def compose(candidates, units, media, top_k):
    by_id = {unit.id: unit for unit in units}
    results, used = [], 0
    for candidate in candidates:
        if len(results) >= top_k:
            break
        unit = by_id[candidate.id]
        size = sum(media.size(key) for key in unit.media_keys)
        if used + size > MAX_MEDIA:
            continue
        content = unit.content
        if isinstance(content, list):
            content = [part if part["type"] == "text" else {
                "type": "image_url", "image_url": {"url": media.uri(part["media_key"], part["mime"])}
            } for part in content]
        results.append({"id": unit.id, "content": content, "score": candidate.score,
                        "created_at": unit.created_at})
        used += size
    return {"data": results}
