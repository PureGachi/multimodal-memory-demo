import base64
import io
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from memory_app.config import Settings
from memory_app.main import create_app
from memory_app.memory.errors import ModelUnavailable


def image_uri():
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def payload(request="r1", user="alice", content="3号机床红色告警灯"):
    return {"request_id": request, "user_id": user, "session_id": "s1", "messages": [
        {"role": "user", "timestamp": 1704067200000, "content": content}
    ]}


def search(client, query="红色告警灯", user="alice", top_k=3):
    return client.post("/search", json={"query": query, "user_id": user, "top_k": top_k})


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, embeddings="none"))) as value:
        yield value


def test_idempotency_conflict_and_restart(tmp_path):
    settings = Settings(data_dir=tmp_path, embeddings="none")
    with TestClient(create_app(settings)) as client:
        body = payload()
        expected = {"success": True, "request_id": "r1", "user_id": "alice", "session_id": "s1"}
        assert client.post("/add", json=body).json() == expected
        assert client.post("/add", json=body).json() == expected
        first = search(client).json()["data"]
        assert len(first) == 1
        assert client.post("/add", json=payload(content="different")).status_code == 409
    with TestClient(create_app(settings)) as restarted:
        assert search(restarted).json()["data"] == first


def test_isolation_top_k_and_empty(client):
    for i in range(5):
        assert client.post("/add", json=payload(f"r{i}")).status_code == 200
    client.post("/add", json=payload("bob", "bob", "红色告警灯仅属于Bob"))
    results = search(client, top_k=2).json()["data"]
    assert len(results) == 2
    assert all("Bob" not in result["content"] for result in results)
    assert search(client, user="missing").json() == {"data": []}
    assert search(client, top_k=0).json() == {"data": []}
    assert search(client, top_k=-1).status_code == 422


def test_multimodal_order_and_image_query(client):
    uri = image_uri()
    content = [{"type": "text", "text": "红色告警灯"},
               {"type": "image_url", "image_url": {"url": uri}},
               {"type": "text", "text": "这是3号机床"}]
    assert client.post("/add", json=payload(content=content)).status_code == 200
    assert search(client).json()["data"][0]["content"] == content
    image_query = [{"type": "image_url", "image_url": {"url": uri}}]
    assert search(client, image_query).json()["data"][0]["content"] == content
    assert search(client, image_query, user="bob").json() == {"data": []}


@pytest.mark.parametrize("content", [" ", [], [{"type": "video", "url": "bad"}],
    [{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}],
    [{"type": "image_url", "image_url": {"url": "data:image/png;base64,YmFk"}}]])
def test_invalid_content_not_committed(client, content):
    response = client.post("/add", json=payload(content=content))
    assert response.status_code == 422
    assert search(client).json() == {"data": []}
    assert "data:image" not in response.text


def test_auth(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, embeddings="none", api_key="secret"))) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/add", json=payload()).status_code == 401
        assert client.post("/add", json=payload(), headers={"X-Api-Key": "secret"}).status_code == 200
        assert client.post("/search", json={"query": "红灯", "user_id": "alice", "top_k": 1},
                           headers={"Authorization": "Bearer secret"}).status_code == 200


def test_response_media_budget(client, monkeypatch):
    from memory_app.media.local import LocalMediaStore
    uri = image_uri()
    content = [{"type": "text", "text": "红色告警灯"},
               {"type": "image_url", "image_url": {"url": uri}}]
    for i in range(4):
        assert client.post("/add", json=payload(f"image-{i}", content=content)).status_code == 200
    # Simulate valid 10 MiB images without allocating a large fixture.
    monkeypatch.setattr(LocalMediaStore, "size", lambda self, key: 10 * 1024 * 1024)
    assert len(search(client, top_k=4).json()["data"]) == 3


def test_concurrent_retries(client):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post("/add", json=payload()), range(8)))
    assert all(response.status_code == 200 for response in responses)
    assert len(search(client).json()["data"]) == 1


class ControlledEmbedder:
    version = "test-v1"
    fail = False

    def embed(self, texts, *, query=False):
        if self.fail:
            raise ModelUnavailable("test failure")
        return [[1.0, 0.0] if "键盘" in text or "输入设备" in text else [0.0, 1.0] for text in texts]


def test_dense_persistence_failure_and_fallback(tmp_path):
    embedder = ControlledEmbedder()
    settings = Settings(data_dir=tmp_path, embeddings="ollama")
    with TestClient(create_app(settings, embedder=embedder)) as client:
        assert client.post("/add", json=payload(content="我最喜欢蓝色键盘")).status_code == 200
        assert search(client, "输入设备").json()["data"]
        embedder.fail = True
        assert client.post("/add", json=payload("r2", content="红色告警灯")).status_code == 503
        assert search(client, "蓝色键盘").json()["data"]  # lexical fallback
        assert len(client.app.state.service.dependencies[0].list_units("alice")) == 1
    embedder.fail = False
    with TestClient(create_app(settings, embedder=embedder)) as restarted:
        assert search(restarted, "输入设备").json()["data"]
