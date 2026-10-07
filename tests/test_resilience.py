"""Boundary and failure scenarios beyond the first smoke test."""
import base64
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from memory_app.config import Settings
from memory_app.main import create_app
from memory_app.embeddings.ollama import OllamaEmbedder
from memory_app.memory.errors import ModelUnavailable


def body(request="r", content="红色告警灯", user="alice"):
    return {"request_id": request, "user_id": user, "session_id": "s", "messages": [
        {"role": "user", "content": content}
    ]}


def query(client, text="红色告警灯", user="alice"):
    return client.post("/search", json={"query": text, "user_id": user, "top_k": 3})


def picture(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, fmt)
    mime = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}[fmt]
    return f"data:image/{mime};base64," + base64.b64encode(stream.getvalue()).decode()


@pytest.mark.parametrize("user", [" ", "\t", "\n"])
def test_blank_search_scope_is_rejected(tmp_path, user):
    with TestClient(create_app(Settings(tmp_path, "none"))) as client:
        assert query(client, user=user).status_code == 422


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_supported_image_formats_roundtrip(tmp_path, fmt):
    content = [{"type": "image_url", "image_url": {"url": picture(fmt)}},
               {"type": "text", "text": "红色告警灯"}]
    with TestClient(create_app(Settings(tmp_path, "none"))) as client:
        assert client.post("/add", json=body(content=content)).status_code == 200
        assert query(client).json()["data"][0]["content"] == content


def test_false_mime_and_multi_message_atomic_validation(tmp_path):
    with TestClient(create_app(Settings(tmp_path, "none"))) as client:
        bad_uri = picture().replace("image/png", "image/jpeg")
        payload = body()
        payload["messages"].append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": bad_uri}}
        ]})
        assert client.post("/add", json=payload).status_code == 422
        assert query(client).json() == {"data": []}
        assert not list((tmp_path / "media").rglob("pending-*"))


def test_failed_database_write_is_retryable_without_partial_units(tmp_path, monkeypatch):
    app = create_app(Settings(tmp_path, "none"))
    vector_store = app.state.service.dependencies[3]
    original = vector_store.insert

    def fail(*args):
        raise sqlite3.OperationalError("injected disk/lock failure with private internals")

    with TestClient(app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(vector_store, "insert", fail)
        failed = client.post("/add", json=body())
        assert failed.status_code == 503
        assert "private internals" not in failed.text
        assert query(client).json() == {"data": []}
        monkeypatch.setattr(vector_store, "insert", original)
        assert client.post("/add", json=body()).status_code == 200
        assert len(query(client).json()["data"]) == 1


def test_missing_media_has_clear_service_error(tmp_path):
    content = [{"type": "text", "text": "红色告警灯"},
               {"type": "image_url", "image_url": {"url": picture()}}]
    app = create_app(Settings(tmp_path, "none"))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/add", json=body(content=content)).status_code == 200
        media = app.state.service.dependencies[1]
        unit = app.state.service.dependencies[0].list_units("alice")[0]
        key = unit.media_keys[0]
        (media.root / key).unlink()  # Only a temporary pytest fixture image.
        response = query(client)
        assert response.status_code == 503
        assert str(tmp_path) not in response.text
        # Replay the original request to repair media, without duplicating units.
        assert client.post("/add", json=body(content=content)).status_code == 200
        assert len(query(client).json()["data"]) == 1


def test_concurrent_conflicting_writes_and_same_id_different_users(tmp_path):
    with TestClient(create_app(Settings(tmp_path, "none"))) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda text: client.post("/add", json=body(content=text)),
                                      ["红色告警灯", "蓝色告警灯"]))
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert client.post("/add", json=body(user="bob")).status_code == 200
        store = client.app.state.service.dependencies[0]
        assert len(store.list_units("alice")) == len(store.list_units("bob")) == 1
        assert store.list_units("alice")[0].id != store.list_units("bob")[0].id


def test_malformed_ollama_startup_is_controlled(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, json={"models": ["invalid-entry"]}, request=httpx.Request("GET", "http://local/api/tags")))
    with pytest.raises(ModelUnavailable):
        OllamaEmbedder("http://local", "nomic-embed-text:latest")


@pytest.mark.parametrize("vector", [[True, False], [0.0, 0.0], [float("nan"), 1.0], ["bad", 1]])
def test_invalid_ollama_vectors_are_rejected(monkeypatch, vector):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, json={"models": [{"name": "model", "digest": "abc"}]},
        request=httpx.Request("GET", "http://local/api/tags")))
    # NaN is represented directly to simulate a malformed provider response.
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embeddings": [vector]}

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: Response())
    with pytest.raises(ModelUnavailable):
        OllamaEmbedder("http://local", "model").embed(["test"])


def test_long_message_tail_retrieval_preserves_original_and_dense_scope(tmp_path):
    class Embedder:
        version = "long-message-test"

        def embed(self, texts, *, query=False):
            return [[1.0, 0.0] if "尾部秘密" in text or "最后的信息" in text else [0.0, 1.0]
                    for text in texts]

    text = "普通历史内容。" * 150 + "尾部秘密：设备代号是M42。"
    with TestClient(create_app(Settings(tmp_path, "ollama"), embedder=Embedder())) as client:
        assert client.post("/add", json=body(content=text)).status_code == 200
        assert client.post("/add", json=body(content="尾部秘密：仅属于Bob。", user="bob")).status_code == 200
        result = query(client, "最后的信息").json()["data"]
        assert len(result) == 1
        assert result[0]["content"] == text


def test_model_version_change_does_not_mix_vectors(tmp_path):
    class Embedder:
        version = "v1"

        def embed(self, texts, *, query=False):
            return [[1.0, 0.0] for text in texts]

    settings = Settings(tmp_path, "ollama")
    embedder = Embedder()
    with TestClient(create_app(settings, embedder=embedder)) as client:
        assert client.post("/add", json=body(content="蓝色键盘")).status_code == 200
        assert query(client, "输入设备").json()["data"]
    embedder.version = "v2"
    with TestClient(create_app(settings, embedder=embedder)) as client:
        assert query(client, "输入设备").json() == {"data": []}
        assert query(client, "蓝色键盘").json()["data"]  # original text is still available
