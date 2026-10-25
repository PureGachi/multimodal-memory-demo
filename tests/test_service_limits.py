import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from memory_app.api.limits import ServiceLimits
from memory_app.config import Settings
from memory_app.main import create_app


def test_auth_before_body_and_declared_length_rejection(tmp_path):
    settings = Settings(tmp_path, "none", api_key="test-only", max_request_bytes=256)
    with TestClient(create_app(settings)) as client:
        response = client.post("/add", content=b"private"*1000)
        assert response.status_code == 401 and "private" not in response.text
        response = client.post("/add", content=b"private"*1000, headers={"X-Api-Key":"test-only"})
        assert response.status_code == 413 and "private" not in response.text
        assert client.get("/health").status_code == 200


def test_chunked_input_cannot_bypass_limit_and_capacity_recovers(tmp_path):
    settings = Settings(tmp_path, "none", max_request_bytes=100)
    with TestClient(create_app(settings)) as client:
        response = client.post("/add", content=iter([b"a"*70,b"b"*70]))
        assert response.status_code == 413
        assert client.post("/search", json={"query":"x","user_id":"u","top_k":100}).json() == {"data":[]}


def test_concurrency_body_timeout_and_slot_release():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            await receive()
            entered.set()
            await release.wait()
        middleware = ServiceLimits(app, api_key="", max_bytes=100, max_inflight=1, body_timeout=.02)
        scope={"type":"http","method":"POST","path":"/add","headers":[]}
        async def receive():return {"type":"http.request","body":b"{}"}
        first=[]
        async def send_first(value):first.append(value)
        task=asyncio.create_task(middleware(scope,receive,send_first))
        await entered.wait()
        sent=[]
        async def send(value):sent.append(value)
        await middleware(scope,receive,send)
        assert sent[0]["status"] == 503
        release.set()
        await task
        assert middleware.inflight == 0
        async def slow():
            await asyncio.sleep(.2)
            return {"type":"http.request","body":b"{}"}
        sent.clear()
        await middleware(scope,slow,send)
        assert sent[0]["status"] == 408 and middleware.inflight == 0
    asyncio.run(run())


@pytest.mark.parametrize("changes", [{"max_request_bytes":0},{"max_inflight":0},{"max_inflight":65},
    {"body_timeout_seconds":float('nan')},{"body_timeout_seconds":0}])
def test_invalid_capacity_configuration(changes):
    with pytest.raises(ValueError):replace(Settings(), **changes).validate()


def test_production_does_not_expose_docs_or_local_reports(tmp_path):
    class Embedder:
        version="controlled"
        def embed(self,*args,**kwargs):pytest.fail("no model calls expected")
    settings = Settings(tmp_path, "compatible", api_key="test-only", profile="competition")
    with TestClient(create_app(settings, embedder=Embedder())) as client:
        for path in ("/docs","/redoc","/openapi.json","/data","/api/reports"):
            assert client.get(path).status_code == 404
        assert client.get("/health").status_code == 200


def test_validation_discriminator_does_not_reflect_private_input(tmp_path):
    with TestClient(create_app(Settings(tmp_path,"none"))) as client:
        private="sk-private-invalid-content-type"
        response=client.post("/add",json={"request_id":"r","user_id":"u","session_id":"s",
            "messages":[{"role":"user","content":[{"type":private,"text":private}]}]})
        assert response.status_code==422 and private not in response.text
        assert any(item["type"]=="union_tag_invalid" for item in response.json()["detail"])
