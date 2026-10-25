import logging
from contextlib import asynccontextmanager, ExitStack
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from memory_app import __version__
from memory_app.config import Settings
from memory_app.errors import error_feedback
from memory_app.api.routes import build_router
from memory_app.api.limits import ServiceLimits
from memory_app.embeddings.factory import build_embedder
from memory_app.media.local import LocalMediaStore
from memory_app.memory.errors import Conflict, InvalidContent, ModelUnavailable, StorageUnavailable
from memory_app.memory.service import MemoryService
from memory_app.storage.sqlite import SQLiteStore
from memory_app.vector_stores.sqlite import SQLiteVectorStore
from memory_app.vision.factory import build_vision


def create_app(settings: Settings | None = None, *, embedder=None, vision=None):
    settings = settings or Settings.from_env()
    settings.validate()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    vision = vision if vision is not None else build_vision(settings)
    embedder = embedder if embedder is not None else build_embedder(settings)
    store = SQLiteStore(settings.data_dir / "memory.sqlite3")
    media = LocalMediaStore(settings.data_dir / "media")
    service = MemoryService(store, media, embedder, SQLiteVectorStore(store), vision)
    @asynccontextmanager
    async def lifespan(app):
        with ExitStack() as stack:
            for dependency in (embedder, vision):
                close = getattr(dependency, "close", None)
                if close is not None:
                    stack.callback(close)
            yield
    private_docs = settings.profile != "competition"
    app = FastAPI(title="Multimodal Memory Demo", version=__version__,
                  lifespan=lifespan,
                  docs_url="/docs" if private_docs else None,
                  redoc_url="/redoc" if private_docs else None,
                  openapi_url="/openapi.json" if private_docs else None)
    app.state.service = service

    async def error_handler(request: Request, exc):
        status = 409 if isinstance(exc, Conflict) else 422 if isinstance(exc, InvalidContent) else 503
        feedback = error_feedback(exc)
        return JSONResponse(status_code=status, content={"detail": feedback["message"], "error": feedback})

    for error in (Conflict, InvalidContent, ModelUnavailable, StorageUnavailable):
        app.add_exception_handler(error, error_handler)
    # Avoid FastAPI's default validation response echoing raw Base64 or credentials.
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        messages = {"missing":"缺少必填字段", "json_invalid":"请求不是合法 JSON",
                    "union_tag_invalid":"内容类型必须是 text 或 image_url",
                    "union_tag_not_found":"缺少内容类型 type", "literal_error":"字段取值不在允许列表",
                    "string_too_short":"字符串不得为空", "too_short":"列表不得为空",
                    "int_type":"字段必须是整数", "greater_than_equal":"字段小于允许下限",
                    "less_than_equal":"字段大于允许上限", "value_error":"字段值不符合要求"}
        fields = {"body","request_id","messages","user_id","session_id","role","content","timestamp",
                  "type","text","image_url","url","query","top_k","options"}
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": [part if type(part) is int or part in fields else "field" for part in item["loc"]],
             "type": item["type"] if item["type"] in messages else "invalid_input",
             "msg": messages.get(item["type"], "输入不符合字段要求")}
            for item in exc.errors()
        ]})

    app.include_router(build_router(service, settings.api_key, settings.embeddings))
    app.add_middleware(ServiceLimits, api_key=settings.api_key,
                       max_bytes=settings.max_request_bytes, max_inflight=settings.max_inflight,
                       body_timeout=settings.body_timeout_seconds)
    return app
