from fastapi import APIRouter, Depends, HTTPException, Request
from memory_app import __version__
from memory_app.api.schemas import AddRequest, SearchRequest
from memory_app.api.limits import valid_credentials


def build_router(service, api_key, mode):
    router = APIRouter()

    def authorize(request: Request):
        if not api_key:
            return
        provided = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        if not valid_credentials(api_key, provided, auth):
            raise HTTPException(401, "Invalid API key")

    @router.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "embeddings": mode,
                "image_analysis": service.vision is not None}

    @router.post("/add", dependencies=[Depends(authorize)])
    def add(body: AddRequest):
        return service.add(body.model_dump(mode="json"))

    @router.post("/search", dependencies=[Depends(authorize)])
    def search(body: SearchRequest):
        return service.search(body.model_dump(mode="json"))

    return router
