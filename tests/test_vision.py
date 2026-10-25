import base64
import io
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import httpx2
from openai import DefaultHttpxClient
import memory_app.llms.compatible as vision_client_module
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from memory_app.config import Settings
from memory_app.main import create_app
from memory_app.memory.errors import ModelUnavailable
from memory_app.memory.backfill import backfill_images
from memory_app.memory.models import Unit
from memory_app.storage.sqlite import SQLiteStore
from memory_app.llms.compatible import CompatibleVisionClient
from memory_app.vision.base import ImageAnalysis
from memory_app.vision.caption import CaptionAnalyzer
from memory_app.vision.ocr import TesseractAnalyzer
from memory_app.vision.pipeline import ImagePipeline


def picture(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, "PNG")
    return {"type": "image_url", "image_url": {"url":
        "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()}}


def body(request="r1", user="alice", content=None):
    return {"user_id": user, "request_id": request, "session_id": "s", "messages": [
        {"role": "user", "content": content if content is not None else [picture()]}]}


def search(client, query="M42", user="alice"):
    return client.post("/search", json={"user_id": user, "query": query, "top_k": 5})


class Analyzer:
    version = "controlled-vision-v1"

    def __init__(self):
        self.calls = 0
        self.fail = False

    def analyze(self, raw, mime):
        self.calls += 1
        if self.fail:
            raise ModelUnavailable("controlled failure")
        return ImageAnalysis("red warning lamp", "M42")


class Embedder:
    version = "controlled-embed-v1"

    def embed(self, texts, *, query=False):
        return [[1., 0.] if "lamp" in text or "灯" in text else [0., 1.] for text in texts]


def test_image_to_text_search_preserves_original_and_source_vectors(tmp_path):
    vision = Analyzer()
    app = create_app(Settings(tmp_path, "ollama"), embedder=Embedder(), vision=vision)
    content = [{"type": "text", "text": "attached"}, picture(), picture("blue")]
    with TestClient(app) as client:
        assert client.post("/add", json=body(content=content)).status_code == 200
        assert search(client).json()["data"][0]["content"] == content
        assert search(client, "灯").json()["data"][0]["content"] == content
        store = app.state.service.dependencies[0]
        unit = store.list_units("alice")[0]
        assert unit.text == "attached"
        assert len(unit.image_artifacts) == 2
        assert len({a["id"] for a in unit.image_artifacts}) == 2
        assert {a["media_key"] for a in unit.image_artifacts} == set(unit.media_keys)
        with store.connect() as conn:
            sources = {row[0] for row in conn.execute("SELECT source FROM vectors")}
        assert len(sources) == 5  # original + two kinds for each image
        assert len(search(client).json()["data"]) == 1
    with TestClient(create_app(Settings(tmp_path, "none"))) as restarted:
        assert search(restarted).json()["data"][0]["content"] == content


def test_cache_scope_replay_and_model_revision(tmp_path):
    vision = Analyzer()
    app = create_app(Settings(tmp_path, "none"), vision=vision)
    with TestClient(app) as client:
        assert client.post("/add", json=body(content=[picture(), picture()])).status_code == 200
        assert vision.calls == 1
        assert client.post("/add", json=body(content=[picture(), picture()])).status_code == 200
        assert client.post("/add", json=body("r2")).status_code == 200
        assert vision.calls == 1
        assert client.post("/add", json=body("r2", "bob")).status_code == 200
        assert vision.calls == 2
        assert search(client, user="missing").json() == {"data": []}
        vision.version = "controlled-vision-v2"
        assert client.post("/add", json=body("r3")).status_code == 200
        assert vision.calls == 3


def test_vision_failure_no_partial_records_and_retry(tmp_path):
    vision = Analyzer()
    vision.fail = True
    app = create_app(Settings(tmp_path, "none"), vision=vision)
    with TestClient(app) as client:
        assert client.post("/add", json=body()).status_code == 503
        store = app.state.service.dependencies[0]
        assert store.list_units("alice") == []
        with store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM image_analyses").fetchone()[0] == 0
        assert not [p for p in (tmp_path / "media").rglob("*") if p.is_file()]
        vision.fail = False
        assert client.post("/add", json=body()).status_code == 200


def test_database_failure_rolls_back_analysis_and_retry(tmp_path, monkeypatch):
    vision = Analyzer()
    app = create_app(Settings(tmp_path, "none"), vision=vision)
    store, _, _, vectors = app.state.service.dependencies
    original = vectors.insert
    def fail(*args):
        raise sqlite3.OperationalError("controlled rollback")
    with TestClient(app) as client:
        monkeypatch.setattr(vectors, "insert", fail)
        assert client.post("/add", json=body()).status_code == 503
        with store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM image_analyses").fetchone()[0] == 0
        monkeypatch.setattr(vectors, "insert", original)
        assert client.post("/add", json=body()).status_code == 200


def test_validate_all_messages_before_vision(tmp_path):
    vision = Analyzer()
    with TestClient(create_app(Settings(tmp_path, "none"), vision=vision)) as client:
        data = body()
        data["messages"].append({"role": "user", "content": " "})
        assert client.post("/add", json=data).status_code == 422
        assert vision.calls == 0


def test_query_analysis_is_ephemeral_and_falls_back(tmp_path):
    vision = Analyzer()
    app = create_app(Settings(tmp_path, "none"), vision=vision)
    with TestClient(app) as client:
        assert client.post("/add", json=body(content="M42 red warning lamp")).status_code == 200
        assert search(client, [picture()]).json()["data"]
        store = app.state.service.dependencies[0]
        assert len(store.list_units("alice")) == 1
        with store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM image_analyses").fetchone()[0] == 0
        assert not [p for p in (tmp_path / "media").rglob("*") if p.is_file()]
        assert client.post("/add", json=body("r2")).status_code == 200
        vision.fail = True
        # New query image cannot use cache; text still works on model failure.
        mixed = [{"type": "text", "text": "M42"}, picture("blue")]
        assert search(client, mixed).status_code == 200
        assert search(client, mixed).json()["data"]


def test_concurrent_replay_is_single_committed_unit(tmp_path):
    # Admit all storage retries here; API overload rejection is a separate test.
    app = create_app(Settings(tmp_path, "none", max_inflight=8), vision=Analyzer())
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: client.post("/add", json=body()), range(8)))
        assert all(r.status_code == 200 for r in responses)
        assert len(search(client).json()["data"]) == 1
        with app.state.service.dependencies[0].connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM image_analyses").fetchone()[0] == 1


def test_backfill_old_images_is_repeatable(tmp_path):
    app = create_app(Settings(tmp_path, "ollama"), embedder=Embedder())
    with TestClient(app) as client:
        assert client.post("/add", json=body()).status_code == 200
        assert search(client).json()["data"] == []  # no text/derived vectors
        vision = Analyzer()
        assert backfill_images(*app.state.service.dependencies, vision, "alice") == 1
        assert backfill_images(*app.state.service.dependencies, vision, "alice") == 1
        assert vision.calls == 1
        assert len(search(client).json()["data"]) == 1
        assert client.post("/add", json=body()).status_code == 200
        with app.state.service.dependencies[0].connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 2


def test_v01_database_migration_preserves_old_vectors(tmp_path):
    db = tmp_path / "memory.sqlite3"
    old = Unit("old", "alice", "s", "user", None, "hello", "hello", "then")
    value = asdict(old)
    del value["image_artifacts"]
    with sqlite3.connect(db) as conn:
        conn.executescript("""CREATE TABLE units(id TEXT PRIMARY KEY,user_id TEXT,payload TEXT);
            CREATE TABLE vectors(unit_id TEXT,user_id TEXT,version TEXT,chunk_order INTEGER,value TEXT,
            PRIMARY KEY(unit_id,version,chunk_order));
            CREATE INDEX vectors_user ON vectors(user_id,version);""")
        conn.execute("INSERT INTO units VALUES (?,?,?)", ("old", "alice", json.dumps(value)))
        conn.execute("INSERT INTO vectors VALUES (?,?,?,?,?)", ("old", "alice", "v1", 0, "[1,0]"))
    for _ in range(2):
        store = SQLiteStore(db)
        assert store.list_units("alice")[0].image_artifacts == []
        with store.connect() as conn:
            assert conn.execute("SELECT value,source FROM vectors").fetchall() == [("[1,0]", "text")]


def api_client():
    return CompatibleVisionClient("https://vision.invalid/v1", "private-key", "test-model")


def mock_vision_transport(monkeypatch, handler):
    """Exercise SDK serialization/auth/error parsing without external requests."""
    monkeypatch.setattr(vision_client_module, "DefaultHttpxClient", lambda **kwargs:
                        DefaultHttpxClient(transport=httpx2.MockTransport(handler), **kwargs))


def test_remote_payload_and_parsed_output(monkeypatch):
    def post(request):
        assert str(request.url) == "https://vision.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private-key"
        payload = json.loads(request.content)
        assert payload["messages"][1]["content"][1]["image_url"]["url"] == "data:image/png;base64,YWJj"
        contract = payload["response_format"]
        assert contract["type"] == "json_schema"
        assert contract["json_schema"]["strict"] is True
        schema = contract["json_schema"]["schema"]
        assert schema["required"] == ["description", "visible_text"]
        assert schema["additionalProperties"] is False
        return httpx2.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"description":"red lamp","visible_text":"M42"}'}}]})
    mock_vision_transport(monkeypatch, post)
    result = CaptionAnalyzer(api_client()).analyze(b"abc", "image/png")
    assert result == ImageAnalysis("red lamp", "M42")


@pytest.mark.parametrize("output", ['{}', '[]', '{"description":2,"visible_text":""}',
                                    '{"description":"","visible_text":""}', '```json\n{}\n```'])
def test_invalid_caption_output_is_not_success(output):
    client = api_client()
    client.complete = lambda *args: output
    with pytest.raises(ModelUnavailable):
        CaptionAnalyzer(client).analyze(b"abc", "image/png")


def test_explicit_json_object_mode_and_cache_identity(monkeypatch):
    def handler(request):
        assert json.loads(request.content)["response_format"] == {"type": "json_object"}
        return httpx2.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"description":"red lamp","visible_text":"M42"}'}}]})
    mock_vision_transport(monkeypatch, handler)
    client = CompatibleVisionClient("https://vision.invalid/v1", "private-key", "test-model", "json_object")
    analyzer = CaptionAnalyzer(client)
    assert analyzer.analyze(b"abc", "image/png").visible_text == "M42"
    assert analyzer.version != CaptionAnalyzer(api_client()).version
    assert analyzer.provenance["response_format"] == "json_object"


@pytest.mark.parametrize("content", [
    '```json\n{"description":"private-data","visible_text":"M42"}\n```',
    'private-data', '{"description":"private-data"',
    '{"description":"private-data","visible_text":"M42","extra":"private-data"}',
    '{"description":null,"visible_text":"private-data"}',
    '{"description":"' + 'x' * 16001 + '","visible_text":"private-data"}',
])
def test_provider_ignoring_schema_is_rejected_without_output_leak(monkeypatch, content):
    mock_vision_transport(monkeypatch, lambda request: httpx2.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": content}}]}))
    with pytest.raises(ModelUnavailable) as error:
        CaptionAnalyzer(api_client()).analyze(b"abc", "image/png")
    assert "private-data" not in str(error.value)
    assert error.value.__suppress_context__


def test_refusal_never_becomes_image_evidence(monkeypatch):
    mock_vision_transport(monkeypatch, lambda request: httpx2.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {
            "content": '{"description":"red lamp","visible_text":"M42"}',
            "refusal": "private-data"}}]}))
    with pytest.raises(ModelUnavailable, match="refused") as error:
        CaptionAnalyzer(api_client()).analyze(b"abc", "image/png")
    assert "private-data" not in str(error.value)


def test_unsupported_schema_does_not_trigger_paid_fallback(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx2.Response(400, json={"error": {"code": "unsupported_parameter", "message": "private-data"}})
    mock_vision_transport(monkeypatch, handler)
    with pytest.raises(ModelUnavailable, match="VISION_RESPONSE_FORMAT=json_schema"):
        api_client().complete("prompt", b"abc", "image/png")
    assert len(calls) == 1


def test_invalid_response_format_rejected_before_network():
    with pytest.raises(ValueError, match="VISION_RESPONSE_FORMAT"):
        CompatibleVisionClient("https://vision.invalid/v1", "private-key", "test-model", "text")


@pytest.mark.parametrize("status,choices", [(429, []), (200, []), (200, [{"finish_reason": "length"}])])
def test_remote_errors_hide_private_data(monkeypatch, status, choices):
    mock_vision_transport(monkeypatch, lambda request: httpx2.Response(status,
        json={"choices": choices, "error": "private-key/private-data"}))
    with pytest.raises(ModelUnavailable) as error:
        api_client().complete("prompt", b"abc", "image/png")
    assert "private" not in str(error.value)


@pytest.mark.parametrize("code,expected", [("insufficient_quota", "insufficient_quota"),
                                         ("rate_limit_exceeded", "rate_limit_exceeded"),
                                         ("private-key/private-data", "unclassified")])
def test_sdk_error_code_is_safe_and_retry_disabled(monkeypatch, code, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx2.Response(429, json={"error": {"code": code,
            "type": "insufficient_quota", "message": "private-key/private-data"}})
    mock_vision_transport(monkeypatch, handler)
    with pytest.raises(ModelUnavailable) as error:
        api_client().complete("prompt", b"abc", "image/png")
    assert len(calls) == 1
    assert f"HTTP 429, code={expected}" in str(error.value)
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_sdk_connection_failure_is_sanitized_without_retry(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx2.ConnectError("private-key/private-data", request=request)
    mock_vision_transport(monkeypatch, handler)
    with pytest.raises(ModelUnavailable) as error:
        api_client().complete("prompt", b"abc", "image/png")
    assert len(calls) == 1
    assert "private" not in str(error.value)


def test_sdk_has_no_global_state_and_closes_http_client(monkeypatch):
    created = []
    def make_client(**kwargs):
        assert kwargs["trust_env"] is False
        client = DefaultHttpxClient(transport=httpx2.MockTransport(lambda request:
            httpx2.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]})), **kwargs)
        created.append(client)
        return client
    monkeypatch.setattr(vision_client_module, "DefaultHttpxClient", make_client)
    client = api_client()
    assert client.complete("prompt", b"abc", "image/png") == "ok"
    assert client.complete("prompt", b"abc", "image/png") == "ok"
    assert len(created) == 1
    assert not created[0].is_closed
    ImagePipeline([CaptionAnalyzer(client)]).close()
    assert created[0].is_closed
    client.close()
    with pytest.raises(ModelUnavailable):
        client.complete("prompt", b"abc", "image/png")
    assert len(created) == 1


def test_tesseract_adapter_uses_safe_subprocess_and_blank_is_valid(monkeypatch):
    def run(command, **kw):
        assert not kw.get("shell", False)
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, b"tesseract 5.test\n", b"")
        assert command[-4:] == ["-l", "eng", "--psm", "3"]
        return subprocess.CompletedProcess(command, 0, b"", b"")
    monkeypatch.setattr(subprocess, "run", run)
    analyzer = TesseractAnalyzer()
    raw = base64.b64decode(picture()["image_url"]["url"].split(",")[1])
    assert analyzer.analyze(raw, "image/png") == ImageAnalysis()


def test_pipeline_revision_and_configuration_limits(tmp_path):
    first = CaptionAnalyzer(api_client(), "1")
    second = CaptionAnalyzer(api_client(), "2")
    assert ImagePipeline([first]).version != ImagePipeline([second]).version
    with pytest.raises(ValueError):
        CompatibleVisionClient("http://example.com", "secret", "model")
    with pytest.raises(ValueError):
        ImageAnalysis(description="x" * 16001)
    with pytest.raises(ValueError):
        create_app(Settings(data_dir=tmp_path, embeddings="none", vision="typo"))


def test_windows_media_publish_race_only_succeeds_if_target_exists(tmp_path, monkeypatch):
    import os
    from memory_app.media.local import LocalMediaStore
    media = LocalMediaStore(tmp_path)
    key = media.key("alice", b"original image bytes")
    original = os.replace
    def competing_writer(temp, target):
        original(temp, target)  # another writer publishes the identical bytes
        raise PermissionError("Windows destination is in use")
    monkeypatch.setattr(os, "replace", competing_writer)
    media.put(key, b"original image bytes")
    assert (tmp_path / key).read_bytes() == b"original image bytes"
    def genuine_failure(temp, target):
        raise PermissionError("No writer succeeded")
    monkeypatch.setattr(os, "replace", genuine_failure)
    with pytest.raises(PermissionError):
        media.put(media.key("alice", b"other bytes"), b"other bytes")
    assert not list(tmp_path.rglob("pending-*"))


def test_conda_ocr_finds_language_data_without_activation(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    prefix = tmp_path / "conda"
    data = prefix / "share/tessdata"
    data.mkdir(parents=True)
    exe = prefix / "Library/bin/tesseract.exe"
    commands = []
    def run(command, **kw):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"tesseract 5.test\n" if "--version" in command else b"M42", b"")
    monkeypatch.setattr(subprocess, "run", run)
    analyzer = TesseractAnalyzer(str(exe))
    raw = base64.b64decode(picture()["image_url"]["url"].split(",")[1])
    assert analyzer.analyze(raw, "image/png").visible_text == "M42"
    assert commands[-1][3:5] == ["--tessdata-dir", str(data)]


def test_ocr_explicit_language_directory_overrides_conda(tmp_path, monkeypatch):
    explicit = str(tmp_path / "explicit-data")
    monkeypatch.setenv("TESSDATA_PREFIX", explicit)
    monkeypatch.setattr(subprocess, "run", lambda command, **kw:
                        subprocess.CompletedProcess(command, 0, b"tesseract 5.test\n", b""))
    assert TesseractAnalyzer().tessdata_dir == explicit
