from dataclasses import replace
import math

import httpx
import pytest
from fastapi.testclient import TestClient

from memory_app.config import Settings
from memory_app.embeddings.compatible import CompatibleEmbedder
from memory_app.embeddings.factory import build_embedder
from memory_app.main import create_app
from memory_app.memory.errors import ModelUnavailable


@pytest.fixture(autouse=True)
def request_seam(monkeypatch):
    # Existing shape/error tests mock one request; real pooling is independently
    # exercised against HTTPX MockTransport in test_embedding_pool.py.
    monkeypatch.setattr(CompatibleEmbedder, "_post", lambda self, *a, **kw: httpx.post(*a, **kw))


def remote(**kwargs):
    return CompatibleEmbedder("https://embedding.example/v1", "test-secret", dimensions=64, **kwargs)


def rows(batch, dimensions=64):
    return {"data": [{"index": i, "embedding": [3., 4.] + [0.] * (dimensions - 2)}
                     for i in reversed(range(len(batch)))]}


def response(data):
    return httpx.Response(200, json=data, request=httpx.Request("POST", "https://example.com/v1"))


def test_batch_order_normalization_and_query_no_nomic_prefix(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response(rows(kwargs["json"]["input"]))
    monkeypatch.setattr(httpx, "post", post)
    embedder = remote()
    assert embedder.embed([]) == []
    result = embedder.embed([str(i) for i in range(23)], query=True)
    assert [len(call[1]["json"]["input"]) for call in calls] == [10, 10, 3]
    assert calls[0][1]["json"] == {"model": "text-embedding-v4", "input": [str(i) for i in range(10)],
                                  "dimensions": 64, "encoding_format": "float"}
    assert calls[0][0].endswith("/embeddings")
    assert calls[0][1]["trust_env"] is False
    assert len(result) == 23 and result[0][:2] == [.6, .8]
    assert math.hypot(*result[0]) == pytest.approx(1)


@pytest.mark.parametrize("data", [None, [], {"data": []}, {"data": [None]},
    {"data": [{"index": True, "embedding": [1.] * 64}]},
    {"data": [{"index": 1, "embedding": [1.] * 64}]},
    {"data": [{"index": 0, "embedding": [1.] * 63}]},
    {"data": [{"index": 0, "embedding": [True] * 64}]},
    {"data": [{"index": 0, "embedding": [0.] * 64}]},
    {"data": [{"index": 0, "embedding": ["nan"] * 64}]}])
def test_invalid_vectors_fail_closed(monkeypatch, data):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response(data))
    with pytest.raises(ModelUnavailable):
        remote().embed(["private input"])


def test_http_failure_sanitized_no_retry(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(429, text="private input test-secret", request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ModelUnavailable) as error:
        remote().embed(["private input"])
    assert len(calls) == 1
    assert "private input" not in str(error.value) and "test-secret" not in str(error.value)


@pytest.mark.parametrize("url", ["", "http://example.com/v1", "https://user:pw@example.com/v1",
                                 "https://example.com/v1?key=secret", "https://{WorkspaceId}.example/v1"])
def test_invalid_endpoint(url):
    with pytest.raises(ValueError):
        CompatibleEmbedder(url, "secret")


def test_version_changes_with_configuration_not_key():
    first = remote()
    assert first.version == remote().version
    assert first.version != remote(revision="2").version
    assert first.version != CompatibleEmbedder("https://embedding.example/v1", "other-secret", dimensions=128).version
    assert first.version == CompatibleEmbedder("https://embedding.example/v1", "other-secret", dimensions=64).version


def competition(tmp_path):
    return Settings(data_dir=tmp_path, profile="competition", embeddings="compatible",
                    embedding_base_url="https://embedding.example/v1", embedding_api_key="test-secret",
                    embedding_dimensions=64, api_key="service-secret", vision="compatible",
                    vision_base_url="https://vision.example/v1", vision_api_key="vision-secret", vision_model="gpt-4o-mini")


@pytest.mark.parametrize("changes", [{"embeddings": "ollama"}, {"embeddings": "none"},
    {"embedding_model": "nomic-embed-text"}, {"vision_model": "deepseek"},
    {"ocr": "tesseract"}, {"api_key": ""}])
def test_competition_guard_before_storage(tmp_path, changes):
    target = tmp_path / "must-not-create"
    with pytest.raises(ValueError):
        create_app(replace(competition(target), **changes))
    assert not target.exists()


def test_competition_env_defaults(monkeypatch):
    for name in ("MEMORY_DATA_DIR", "MEMORY_EMBEDDINGS", "MEMORY_VISION", "MEMORY_OCR",
                 "VISION_MODEL", "VISION_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MEMORY_PROFILE", "competition")
    monkeypatch.setenv("MEMORY_API_KEY", "service-secret")
    settings = Settings.from_env(env_file=None)
    assert settings.embeddings == "compatible"
    assert str(settings.data_dir).replace("\\", "/") == "data/competition"
    assert settings.embedding_model == "text-embedding-v4"
    assert settings.vision_model == "gpt-4o-mini" and settings.ocr == "none"


def test_mock_full_add_search_with_required_models(monkeypatch, tmp_path):
    import json
    import httpx2
    from test_vision import mock_vision_transport
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs["json"]["model"]))
        if url.endswith("/embeddings"):
            return response(rows(kwargs["json"]["input"]))
        assert kwargs["json"]["messages"][1]["content"][1]["type"] == "image_url"
        return response({"choices": [{"finish_reason": "stop", "message": {
            "content": '{"description":"red lamp","visible_text":"M42"}'}}]})
    monkeypatch.setattr(httpx, "post", post)
    def vision_post(request):
        payload = json.loads(request.content)
        calls.append((str(request.url), payload["model"]))
        assert payload["messages"][1]["content"][1]["type"] == "image_url"
        return httpx2.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"description":"red lamp","visible_text":"M42"}'}}]})
    mock_vision_transport(monkeypatch, vision_post)
    from test_vision import picture
    with TestClient(create_app(competition(tmp_path))) as client:
        headers = {"Authorization": "Bearer service-secret"}
        body = {"user_id": "alice", "request_id": "r", "session_id": "s",
                "messages": [{"role": "user", "content": [picture()]}]}
        assert client.post("/add", json=body, headers=headers).status_code == 200
        result = client.post("/search", headers=headers,
                             json={"user_id": "alice", "query": "M42", "top_k": 5})
        assert result.status_code == 200
        assert result.json()["data"][0]["content"] == body["messages"][0]["content"]
    assert {model for _, model in calls} == {"text-embedding-v4", "gpt-4o-mini"}


def test_missing_remote_key_no_storage(tmp_path):
    target = tmp_path / "must-not-create"
    with pytest.raises(ValueError):
        create_app(replace(competition(target), embedding_api_key=""))
    assert not target.exists()
    assert build_embedder(Settings(embeddings="none")) is None


def test_response_index_order_is_actually_preserved(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response({"data": [
        {"index": 1, "embedding": [0., 1.] + [0.] * 62},
        {"index": 0, "embedding": [1., 0.] + [0.] * 62}]}))
    result = remote().embed(["first", "second"])
    assert result[0][:2] == [1., 0.]
    assert result[1][:2] == [0., 1.]


@pytest.mark.parametrize("ending", ["/embeddings", "/chat/completions"])
def test_full_endpoint_rejected_to_avoid_duplicate_path(ending):
    with pytest.raises(ValueError, match="API base URL"):
        CompatibleEmbedder("https://embedding.example/v1" + ending, "test-key")


def test_http_failure_has_safe_code_and_suppresses_raw_body(monkeypatch):
    def post(url, **kwargs):
        return httpx.Response(401, json={"error": {"code": "invalid_api_key", "message": "private-secret"}},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ModelUnavailable, match="HTTP 401, code=invalid_api_key") as error:
        remote().embed(["private-input"])
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__
