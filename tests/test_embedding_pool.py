import json

import httpx
import pytest
from fastapi.testclient import TestClient
from memory_app.config import Settings
from memory_app.embeddings.compatible import CompatibleEmbedder
from memory_app.main import create_app
from memory_app.memory.errors import ModelUnavailable


def test_reuses_client_across_batches_and_queries_and_closes_once():
    calls=[]
    def handler(request):
        payload=json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200,json={"data":[{"index":i,"embedding":[1.]+[0.]*63}
            for i in range(len(payload["input"]))]})
    client=httpx.Client(transport=httpx.MockTransport(handler),trust_env=False)
    model=CompatibleEmbedder("https://example.test/v1","test-only",dimensions=64,http_client=client)
    assert len(model.embed([str(i) for i in range(23)]))==23
    assert len(model.embed(["query"],query=True))==1
    assert model._client is client and [len(x["input"]) for x in calls]==[10,10,3,1]
    model.close();model.close()
    assert client.is_closed
    with pytest.raises(ModelUnavailable):model.embed(["after close"])


def test_connection_failure_is_not_automatically_retried():
    calls=[]
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("private body",request=request)
    client=httpx.Client(transport=httpx.MockTransport(handler),trust_env=False)
    model=CompatibleEmbedder("https://example.test/v1","test-only",dimensions=64,http_client=client)
    with pytest.raises(ModelUnavailable) as caught:model.embed(["private text"])
    assert len(calls)==1 and caught.value.diagnostic=="connect_failure"
    model.close()


def test_app_lifespan_closes_embedding_client(tmp_path):
    class Vision:
        closed = False
        def close(self):
            self.closed = True
    vision = Vision()
    client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(500)),trust_env=False)
    model=CompatibleEmbedder("https://example.test/v1","test-only",dimensions=64,http_client=client)
    with TestClient(create_app(Settings(tmp_path,"none"),embedder=model,vision=vision)) as api:
        assert api.get("/health").status_code==200
        assert not client.is_closed
        assert not vision.closed
    assert client.is_closed
    assert vision.closed
