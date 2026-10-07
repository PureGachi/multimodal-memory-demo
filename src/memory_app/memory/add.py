import hashlib
import json
import uuid
from datetime import datetime, timezone
from memory_app.media.validation import MAX_MEDIA
from memory_app.memory.errors import InvalidContent, ModelUnavailable
from memory_app.memory.models import Unit
from memory_app.memory.parser import parse_content
from memory_app.memory.units import text_chunks


def add_memory(payload, store, media, embedder, vectors):
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
    units, pending, chunks, owners = [], [], [], []
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
        for chunk_order, chunk in enumerate(text_chunks(text)):
            chunks.append(chunk)
            owners.append((unit_id, chunk_order))
    embedded = embedder.embed(chunks) if embedder else []
    if embedder and len(embedded) != len(chunks):
        raise ModelUnavailable("Embedding count does not match index chunks")
    records = [(unit_id, embedder.version, order, vector)
               for (unit_id, order), vector in zip(owners, embedded)]
    # Model work completes before file writes and the short database transaction.
    for key, raw in pending:
        media.put(key, raw)
    store.commit_add(user_id, request_id, digest, units, records, vectors)
    return response
