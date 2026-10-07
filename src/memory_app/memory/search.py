import logging
from memory_app.memory.errors import ModelUnavailable
from memory_app.memory.models import Candidate
from memory_app.memory.parser import parse_content
from memory_app.memory.units import text_chunks
from memory_app.memory.evidence import compose
from memory_app.retrieval.lexical import lexical_search
from memory_app.retrieval.fusion import rrf

logger = logging.getLogger(__name__)


def search_memory(payload, store, media, embedder, vectors):
    user_id, top_k = payload["user_id"], payload["top_k"]
    _, text, image_keys, _ = parse_content(payload["query"], user_id, media)
    # Options are query hints, never final answers or stored facts.
    text = "\n".join([text, *(payload.get("options") or [])]).strip()
    units = store.list_units(user_id)
    if top_k == 0 or not units:
        return {"data": []}
    depth = max(top_k * 3, 30)
    lists = [lexical_search(units, text, depth)]
    if image_keys:
        lists.append([Candidate(unit.id, 1) for unit in units if set(image_keys) & set(unit.media_keys)])
    if embedder and text:
        try:
            query_vectors = embedder.embed(text_chunks(text), query=True)
            lists.append(vectors.search(user_id, embedder.version, query_vectors, depth))
        except ModelUnavailable:
            logger.warning("Search embedding unavailable; using lexical/image-identity channels")
    return compose(rrf(lists), units, media, top_k)
