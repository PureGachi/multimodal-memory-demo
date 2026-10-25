from memory_app.embeddings.compatible import CompatibleEmbedder
from memory_app.embeddings.ollama import OllamaEmbedder


def build_embedder(settings):
    settings.validate()
    if settings.embeddings == "none":
        return None
    if settings.embeddings == "ollama":
        return OllamaEmbedder(settings.ollama_url, settings.embed_model)
    return CompatibleEmbedder(settings.embedding_base_url, settings.embedding_api_key,
                              settings.embedding_model, settings.embedding_dimensions,
                              settings.embedding_revision)
