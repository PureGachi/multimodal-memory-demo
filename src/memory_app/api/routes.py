import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from memory_app.api.schemas import AddRequest, SearchRequest


def build_router(service, api_key, mode):
    router = APIRouter()

    def authorize(request: Request):
        if not api_key:
            return
        provided = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") or auth.startswith("Token "):
            provided = auth.split(" ", 1)[1]
        if not secrets.compare_digest(provided.encode(), api_key.encode()):
            raise HTTPException(401, "Invalid API key")

    @router.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "embeddings": mode}

    @router.post("/add", dependencies=[Depends(authorize)])
    def add(body: AddRequest):
        return service.add(body.model_dump(mode="json"))

    @router.post("/search", dependencies=[Depends(authorize)])
    def search(body: SearchRequest):
        return service.search(body.model_dump(mode="json"))

    return router
