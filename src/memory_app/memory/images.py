import json
import uuid
from memory_app.vision.pipeline import analyze_cached, analysis_version


def dialogue_context(unit, previous=None):
    """Bounded, causal context from this Add only; never load later turns or QAs."""
    sources = []
    if (previous is not None and unit.role == "assistant" and previous.role == "user"
            and previous.user_id == unit.user_id and previous.session_id == unit.session_id):
        sources.append(previous)
    sources.append(unit)
    messages = [{"message_id": u.id, "role": u.role, "text": u.text[:4000],
                 "truncated": len(u.text) > 4000} for u in sources if u.text.strip()]
    return {"policy": "same-add-current-and-previous-user-v1", "messages": messages,
            "image_count": len(unit.media_keys)} if messages else None


def enrich_images(unit, store, vision, raw_for_key, cache, now, *, context=None):
    """Replace only derived fields; preserve original content/text and media order."""
    artifacts = []
    for part in unit.content if isinstance(unit.content, list) else []:
        if part["type"] != "image_url":
            continue
        key = part["media_key"]
        if any(a["media_key"] == key for a in artifacts):
            continue
        context = context if getattr(vision, "supports_context", False) else None
        version = analysis_version(vision, context)
        value = analyze_cached(store, vision, unit.user_id, key, raw_for_key(key), part["mime"], cache, context=context)
        identity = json.dumps([unit.id, key, version])
        artifacts.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, identity)), "media_key": key,
                          "version": version, "created_at": now,
                          "context_messages": context["messages"] if context else [],
                          "context_policy": context["policy"] if context else None,
                          "providers": getattr(vision, "provenance", [{"version": vision.version}]), **value})
    unit.image_artifacts = artifacts
