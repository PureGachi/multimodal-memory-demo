from datetime import datetime, timezone
from memory_app.memory.images import enrich_images, dialogue_context
from memory_app.memory.indexing import embedding_records


def backfill_images(store, media, embedder, vectors, vision, user_id):
    """Explicit maintenance: stop concurrent service writes first. Per-unit commits."""
    if vision is None:
        raise ValueError("Enable an image analyzer before backfilling")
    cache, count = {}, 0
    for unit in store.list_units(user_id):
        if not unit.media_keys:
            continue
        context = dialogue_context(unit)
        # Preserve exact supplied context on maintenance; never infer adjacency
        # from list_units(), whose ordering is by ID, not conversation time.
        saved = next((a for a in unit.image_artifacts if a.get("context_messages")
                      and a.get("context_policy") == "same-add-current-and-previous-user-v1"), None)
        if saved:
            context = {"policy": saved["context_policy"], "messages": saved["context_messages"],
                       "image_count": len(unit.media_keys)}
        enrich_images(unit, store, vision, lambda key: (media.root / key).read_bytes(), cache,
                      datetime.now(timezone.utc).isoformat(), context=context)
        records = embedding_records([unit], embedder, include_text=False)
        store.replace_image_index(unit, records, vectors)
        count += 1
    return count
