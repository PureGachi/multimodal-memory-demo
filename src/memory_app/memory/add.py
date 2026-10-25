import hashlib
import json
import uuid
from datetime import datetime, timezone
from memory_app.media.validation import MAX_MEDIA
from memory_app.memory.errors import InvalidContent
from memory_app.memory.models import Unit
from memory_app.memory.parser import parse_content
from memory_app.memory.indexing import embedding_records
from memory_app.memory.images import enrich_images, dialogue_context


def add_memory(payload, store, media, embedder, vectors, vision=None):
    user_id, request_id = payload["user_id"], payload["request_id"]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode()).hexdigest()
    response = {"success": True, "request_id": request_id,
                "user_id": user_id, "session_id": payload["session_id"]}
    if store.check_request(user_id, request_id, digest):
        # A replay may also repair missing original media without recomputing vectors.
        for message in payload["messages"]:
            _, _, _, images = parse_content(message["content"], user_id, media)
            for key, raw in images:
                media.put(key, raw)
        return response
    now = datetime.now(timezone.utc).isoformat()
    units, pending, cache = [], [], {}
    total = 0
    for order, message in enumerate(payload["messages"]):
        content, text, keys, images = parse_content(message["content"], user_id, media)
        total += sum(len(raw) for _, raw in images)
        if total > MAX_MEDIA:
            raise InvalidContent("Add images exceed 30 MiB")
        identity = json.dumps([user_id, request_id, order], ensure_ascii=False)
        unit_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        units.append(Unit(unit_id, user_id, payload["session_id"], message["role"],
                          message.get("timestamp"), content, text, now, keys))
        pending.extend(images)
    # Validate the entire request before invoking potentially billed models.
    if vision:
        raw_by_key = dict(pending)
        for index, unit in enumerate(units):
            enrich_images(unit, store, vision, raw_by_key.__getitem__, cache, now,
                          context=dialogue_context(unit, units[index - 1] if index else None))
    records = embedding_records(units, embedder)
    # Model work completes before file writes and the short database transaction.
    for key, raw in pending:
        media.put(key, raw)
    store.commit_add(user_id, request_id, digest, units, records, vectors)
    return response
