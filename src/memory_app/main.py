import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from memory_app.config import Settings
from memory_app.api.routes import build_router
from memory_app.embeddings.ollama import OllamaEmbedder
from memory_app.media.local import LocalMediaStore
from memory_app.memory.errors import Conflict, InvalidContent, ModelUnavailable, StorageUnavailable
from memory_app.memory.service import MemoryService
from memory_app.storage.sqlite import SQLiteStore
from memory_app.vector_stores.sqlite import SQLiteVectorStore


def create_app(settings: Settings | None = None, *, embedder=None):
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    store = SQLiteStore(settings.data_dir / "memory.sqlite3")
    media = LocalMediaStore(settings.data_dir / "media")
    if settings.embeddings == "ollama" and embedder is None:
        embedder = OllamaEmbedder(settings.ollama_url, settings.embed_model)
    service = MemoryService(store, media, embedder, SQLiteVectorStore(store))
    app = FastAPI(title="Multimodal Memory Demo", version="0.1.0")
    app.state.service = service

    async def error_handler(request: Request, exc):
        status = 409 if isinstance(exc, Conflict) else 422 if isinstance(exc, InvalidContent) else 503
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    for error in (Conflict, InvalidContent, ModelUnavailable, StorageUnavailable):
        app.add_exception_handler(error, error_handler)
    # Avoid FastAPI's default validation response echoing raw Base64 or credentials.
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": item["loc"], "type": item["type"], "msg": item["msg"]}
            for item in exc.errors()
        ]})

    app.include_router(build_router(service, settings.api_key, settings.embeddings))
    return app
