from memory_app.memory.units import text_chunks


def index_sources(unit):
    yield "text", unit.text
    for artifact in unit.image_artifacts:
        for kind in ("description", "visible_text", "contextual_description"):
            yield f"image:{artifact['id']}:{kind}", artifact.get(kind, "")


def retrieval_text(unit):
    return "\n".join(text for _, text in index_sources(unit) if text)


def embedding_records(units, embedder, *, include_text=True):
    if embedder is None:
        return []
    texts, owners = [], []
    for unit in units:
        for source, text in index_sources(unit):
            if source == "text" and not include_text:
                continue
            for order, chunk in enumerate(text_chunks(text)):
                texts.append(chunk)
                owners.append((unit.id, source, order))
    embedded = embedder.embed(texts)
    if len(embedded) != len(texts):
        from memory_app.memory.errors import ModelUnavailable
        raise ModelUnavailable("Embedding count does not match index chunks")
    return [(unit_id, embedder.version, order, vector, source)
            for (unit_id, source, order), vector in zip(owners, embedded)]
