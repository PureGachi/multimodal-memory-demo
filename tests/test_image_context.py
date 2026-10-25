"""Controlled fixtures only: no .env, remote models or benchmark answers."""
import json
from dataclasses import replace

import httpx2
import pytest
from fastapi.testclient import TestClient

from memory_app.config import Settings
from memory_app.evaluation.checkpoint import CachedVision, EvaluationCheckpoint
from memory_app.main import create_app
from memory_app.memory.images import dialogue_context
from memory_app.memory.indexing import index_sources, embedding_records
from memory_app.memory.models import Unit
from memory_app.memory.errors import ModelUnavailable
from memory_app.vision.base import ImageAnalysis
from memory_app.vision.caption import CaptionAnalyzer
from memory_app.vision.pipeline import ImagePipeline, analyze_cached, analysis_version
from memory_app.storage.sqlite import SQLiteStore
from test_vision import picture, api_client, mock_vision_transport


class ContextAnalyzer:
    version = "controlled-context-v1"
    supports_context = True

    def __init__(self):
        self.contexts = []
        self.plain_calls = 0

    def analyze(self, raw, mime):
        self.plain_calls += 1
        return ImageAnalysis("abstract colors", "")

    def analyze_with_context(self, raw, mime, context):
        self.contexts.append(context)
        return ImageAnalysis("abstract colors", "", "speaker_claim_unique")


def test_context_sdk_payload_and_separate_strict_fields(monkeypatch):
    context = {"messages": [{"message_id": "m1", "role": "user", "text": "This is my painting."}]}
    def handler(request):
        p = json.loads(request.content)
        assert "This is my painting." not in p["messages"][0]["content"]
        assert "This is my painting." in p["messages"][1]["content"][0]["text"]
        schema = p["response_format"]["json_schema"]["schema"]
        assert schema["required"] == ["description", "visible_text", "contextual_description"]
        assert schema["additionalProperties"] is False
        return httpx2.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"description": "blue strokes", "visible_text": "",
                                   "contextual_description": "The user says this is their painting (m1)."})}}]})
    mock_vision_transport(monkeypatch, handler)
    client = api_client()
    try:
        result = CaptionAnalyzer(client).analyze_with_context(b"abc", "image/png", context)
        assert result.description == "blue strokes"
        assert "user says" in result.contextual_description
    finally:
        client.close()


@pytest.mark.parametrize("value", [
    {"description": "blue", "visible_text": ""},
    {"description": "blue", "visible_text": "", "contextual_description": 3},
    {"description": "blue", "visible_text": "", "contextual_description": "x" * 16001},
    {"description": "blue", "visible_text": "", "contextual_description": "ok", "extra": "secret"},
])
def test_context_schema_is_validated_locally_without_retry(value):
    client = api_client()
    calls = []
    def complete(*args, **kwargs):
        calls.append(kwargs)
        return json.dumps(value)
    client.complete = complete
    with pytest.raises(ModelUnavailable) as error:
        CaptionAnalyzer(client).analyze_with_context(b"abc", "image/png", {"messages": []})
    assert len(calls) == 1 and "secret" not in str(error.value)


def test_add_context_is_causal_persisted_indexed_and_query_stays_plain(tmp_path):
    analyzer = ContextAnalyzer()
    pipeline = ImagePipeline([analyzer])
    app = create_app(Settings(tmp_path, "none"), vision=pipeline)
    messages = [{"role": "user", "content": "Here, this is my painting."},
                {"role": "assistant", "content": [{"type": "text", "text": "Rich colors."}, picture()]},
                {"role": "user", "content": "FUTURE_MESSAGE_MUST_NOT_LEAK"}]
    payload = {"user_id": "alice", "session_id": "s", "request_id": "r", "messages": messages}
    with TestClient(app) as api:
        assert api.post("/add", json=payload).status_code == 200
        assert len(analyzer.contexts) == 1
        context = analyzer.contexts[0]
        assert len(context["messages"]) == 2
        assert "FUTURE_MESSAGE" not in json.dumps(context)
        assert api.post("/add", json=payload).status_code == 200
        assert len(analyzer.contexts) == 1
        result = api.post("/search", json={"user_id": "alice", "query": "speaker_claim_unique", "top_k": 1})
        assert result.status_code == 200
        assert result.json()["data"][0]["content"] == messages[1]["content"]
        store = app.state.service.dependencies[0]
        units = store.list_units("alice")
        unit = next(u for u in units if u.media_keys)
        artifact = unit.image_artifacts[0]
        assert artifact["description"] == "abstract colors"
        assert artifact["contextual_description"] == "speaker_claim_unique"
        assert artifact["context_messages"] == context["messages"]
        assert "speaker_claim_unique" in dict(index_sources(unit)).values()
        class Embedder:
            version = "controlled-context-embedding"
            def embed(self, texts):
                assert "speaker_claim_unique" in texts
                return [[1., 0.] for _ in texts]
        records = embedding_records([unit], Embedder())
        assert any(row[-1].endswith(":contextual_description") for row in records)
        value = store.get_image_analysis("alice", artifact["media_key"], artifact["version"])
        assert value["contextual_description"] == "speaker_claim_unique"
        assert api.post("/search", json={"user_id": "alice", "query": [
            {"type": "text", "text": "Is this MY painting?"}, picture()], "top_k": 1}).status_code == 200
        assert len(analyzer.contexts) == 1 and analyzer.plain_calls == 1
        assert len(store.list_units("alice")) == 3
        from memory_app.memory.backfill import backfill_images
        count = backfill_images(*app.state.service.dependencies, pipeline, "alice")
        assert count == 1 and len(analyzer.contexts) == 1  # persisted cache, same sources


def test_cache_distinguishes_dialogue_and_user_and_reuses_same_context(tmp_path):
    store, analyzer = SQLiteStore(tmp_path / "db.sqlite3"), ContextAnalyzer()
    cache = {}
    first = {"messages": [{"text": "my painting"}]}
    second = {"messages": [{"text": "a museum painting"}]}
    for context, user in ((first, "alice"), (first, "alice"), (second, "alice"), (first, "bob")):
        analyze_cached(store, analyzer, user, "same-image", b"abc", "image/png", cache, context=context)
    assert len(analyzer.contexts) == 3
    assert analysis_version(analyzer, first) != analysis_version(analyzer, second)
    assert analysis_version(analyzer, first) != analyzer.version


def test_checkpoint_reuses_context_result_across_resume(tmp_path):
    analyzer = ContextAnalyzer()
    root = tmp_path / "checkpoint"
    context = {"messages": [{"text": "my painting"}]}
    with EvaluationCheckpoint(root, {"test": "context"}) as checkpoint:
        wrapped = CachedVision(analyzer, checkpoint)
        wrapped.analyze_with_context(b"abc", "image/png", context)
    with EvaluationCheckpoint(root, {"test": "context"}, resume=True) as checkpoint:
        wrapped = CachedVision(analyzer, checkpoint)
        wrapped.analyze_with_context(b"abc", "image/png", context)
        assert len(analyzer.contexts) == 1
        wrapped.analyze_with_context(b"abc", "image/png", {"messages": [{"text": "other"}]})
        wrapped.analyze(b"abc", "image/png")
    assert len(analyzer.contexts) == 2 and analyzer.plain_calls == 1


def test_context_bounds_scope_and_multiple_images():
    user = Unit("m1", "u", "s", "user", None, "", "a" * 5000, "now")
    image = Unit("m2", "u", "s", "assistant", None, [], "reply", "now", ["k1", "k2"])
    context = dialogue_context(image, user)
    assert len(context["messages"][0]["text"]) == 4000
    assert context["messages"][0]["truncated"] and context["image_count"] == 2
    assert len(dialogue_context(image, replace(user, user_id="other"))["messages"]) == 1
    assert len(dialogue_context(image, replace(user, session_id="other"))["messages"]) == 1
    assert len(dialogue_context(image)["messages"]) == 1


def test_context_prompt_changes_analysis_version(monkeypatch):
    import memory_app.vision.caption as caption
    first = CaptionAnalyzer(api_client()).version
    monkeypatch.setattr(caption, "CONTEXT_PROMPT", caption.CONTEXT_PROMPT + " changed")
    assert CaptionAnalyzer(api_client()).version != first
