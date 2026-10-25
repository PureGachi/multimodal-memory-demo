import json

import httpx
import pytest

from memory_app.errors import error_feedback
from memory_app.evaluation.checkpoint import EvaluationCheckpoint, digest
from memory_app.memory.errors import CheckpointError, ModelUnavailable
from memory_app.vision.base import ImageAnalysis
from test_mem_gallery_evaluation import evaluation, multimodal_scene


class Embedder:
    version = "checkpoint-test-v1"
    dimensions = 2

    def __init__(self):
        self.calls = []
        self.fail_query = None
        self.fail_documents = False

    def embed(self, texts, *, query=False):
        self.calls.append((list(texts), query))
        if (query and self.fail_query in texts) or (not query and self.fail_documents):
            raise ModelUnavailable("secret provider response", code="timeout", component="embedding", retryable=True)
        return [[1., 0.] if "keyboard" in text or "M42" in text else [0., 1.] for text in texts]


def scene():
    rounds = [{"round": f"D1:{i}", "session": "D1", "date": None, "has_image": False,
               "messages": [{"role": "user", "content": text}, {"role": "assistant", "content": "remember " + text}]}
              for i, text in enumerate(["blue keyboard", "red lamp"], 1)]
    cases = [{"number": i, "question": text, "clues": [f"D1:{i}"], "answer": "gold", "point": "MR"}
             for i, text in enumerate(["keyboard", "lamp"], 1)]
    return rounds, cases


def identity(rounds, cases, embedder):
    return {"rounds": rounds, "cases": cases, "model": embedder.version, "top_k": 2}


def test_query_failure_resume_reuses_history_and_first_question(tmp_path):
    rounds, cases = scene()
    model = Embedder()
    model.fail_query = "lamp"
    root = tmp_path / "run"
    ident = identity(rounds, cases, model)
    with pytest.raises(ModelUnavailable):
        with EvaluationCheckpoint(root, ident) as checkpoint:
            evaluation.evaluate(rounds, cases, model, top_k=2, checkpoint=checkpoint)
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed" and state["stage"] == "questions"
    assert state["indexed_rounds"] == 2 and state["completed_questions"] == 1
    assert state["question_number"] == 2
    assert state["error"]["code"] == "timeout" and state["error"]["component"] == "embedding"
    assert "secret" not in json.dumps(state)
    initial_calls = len(model.calls)
    model.fail_query = None
    with EvaluationCheckpoint(root, ident, resume=True) as checkpoint:
        report = evaluation.evaluate(rounds, cases, model, top_k=2, checkpoint=checkpoint)
    assert len(model.calls) == initial_calls + 1  # only the failed question
    assert report["indexed_messages"] == 4
    assert report["details"][1]["gold_evidence"][0]["round"] == "D1:2"
    assert {m["round"] for m in report["details"][1]["gold_evidence"][0]["messages"]} == {"D1:2"}
    assert report["summary"]["dense"]["hit_at_k"] == 1


def test_query_vision_is_cached_without_becoming_historical_memory(tmp_path):
    from PIL import Image
    from memory_app.evaluation.gallery_media import image_uri
    from memory_app.storage.sqlite import SQLiteStore
    class Vision:
        version = "query-vision-test"
        calls = 0
        def analyze(self, raw, mime):
            self.calls += 1
            return ImageAnalysis("keyboard", "M42")
    image = tmp_path / "query.png"
    Image.new("RGB", (8, 8)).save(image)
    uri, _ = image_uri(image)
    rounds, cases = scene()
    cases = cases[:1]
    cases[0].update(question="What is this?", query_parts=[
        {"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": uri}}])
    model, vision = Embedder(), Vision()
    model.fail_query = "What is this?\nkeyboard\nM42"
    root, ident = tmp_path / "run", identity(rounds, cases, model)
    with pytest.raises(ModelUnavailable):
        with EvaluationCheckpoint(root, ident) as checkpoint:
            evaluation.evaluate(rounds, cases, model, vision=vision, checkpoint=checkpoint)
    assert vision.calls == 1
    model.fail_query = None
    with EvaluationCheckpoint(root, ident, resume=True) as checkpoint:
        report = evaluation.evaluate(rounds, cases, model, vision=vision, checkpoint=checkpoint)
    assert vision.calls == 1
    assert report["details"][0]["query_image_status"] == "analyzed"
    store = SQLiteStore(root / "memory.sqlite3")
    assert len(store.list_units("mem-gallery-eval")) == 4
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM image_analyses").fetchone()[0] == 0


def test_image_success_survives_embedding_failure(multimodal_scene, tmp_path):
    class Vision:
        version = "test-vision-v1"
        def __init__(self):
            self.calls = 0
        def analyze(self, raw, mime):
            self.calls += 1
            return ImageAnalysis("keyboard", "M42")
    rounds, cases = evaluation.load_scenario(multimodal_scene, with_images=True, assets_dir=tmp_path / "assets")
    model, vision = Embedder(), Vision()
    model.fail_documents = True
    root, ident = tmp_path / "run", identity(rounds, cases, model)
    with pytest.raises(ModelUnavailable):
        with EvaluationCheckpoint(root, ident) as checkpoint:
            evaluation.evaluate(rounds, cases, model, vision=vision, checkpoint=checkpoint)
    assert vision.calls == 1
    model.fail_documents = False
    with EvaluationCheckpoint(root, ident, resume=True) as checkpoint:
        report = evaluation.evaluate(rounds, cases, model, vision=vision, checkpoint=checkpoint)
    assert vision.calls == 1  # no repeat paid vision after failed Add
    assert report["image_summary"]["dense"]["hit_at_k"] == 1


def test_successful_batches_survive_later_batch_failure(tmp_path):
    model = Embedder()
    original = model.embed
    def fail_second(texts, *, query=False):
        if "text-10" in texts:
            raise ModelUnavailable("fail second batch")
        return original(texts, query=query)
    model.embed = fail_second
    root = tmp_path / "run"
    with pytest.raises(ModelUnavailable):
        with EvaluationCheckpoint(root, "same") as checkpoint:
            checkpoint.embedder(model).embed([f"text-{i}" for i in range(12)])
    model.embed = original
    before = len(model.calls)
    with EvaluationCheckpoint(root, "same", resume=True) as checkpoint:
        vectors = checkpoint.embedder(model).embed([f"text-{i}" for i in range(12)])
    assert len(vectors) == 12
    assert len(model.calls) == before + 1 and model.calls[-1][0] == ["text-10", "text-11"]


def test_resume_rejects_changed_identity_before_calls(tmp_path):
    root = tmp_path / "run"
    with EvaluationCheckpoint(root, {"model": "v1", "data": "a"}):
        pass
    before = (root / "state.json").read_text(encoding="utf-8")
    with pytest.raises(CheckpointError):
        with EvaluationCheckpoint(root, {"model": "v2", "data": "a"}, resume=True):
            pytest.fail("must reject")
    assert (root / "state.json").read_text(encoding="utf-8") == before


def test_two_process_handles_cannot_use_same_checkpoint(tmp_path):
    root = tmp_path / "run"
    with EvaluationCheckpoint(root, "same"):
        with pytest.raises(CheckpointError, match="already in use"):
            with EvaluationCheckpoint(root, "same", resume=True):
                pytest.fail("lock must exclude")
    with EvaluationCheckpoint(root, "same", resume=True):
        pass


def test_corrupt_cached_vector_rejected_without_model_call(tmp_path):
    model = Embedder()
    with EvaluationCheckpoint(tmp_path / "run", "same") as checkpoint:
        checkpoint.save_cache("embedding", digest([model.version, False, "keyboard"]), [0., 0.])
        with pytest.raises(CheckpointError):
            checkpoint.embedder(model).embed(["keyboard"])
    assert not model.calls


def test_feedback_never_echoes_arbitrary_exception_text():
    for exc in (RuntimeError("sk-secret private-body"), ValueError("sk-secret private-body"),
                ModelUnavailable("sk-secret private-body")):
        assert "private-body" not in json.dumps(error_feedback(exc))
        assert "sk-secret" not in json.dumps(error_feedback(exc))


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout("private"), "timeout"),
                                          (httpx.ConnectError("private"), "network_error")])
def test_embedding_transport_errors_are_distinguished(monkeypatch, exception, code):
    from memory_app.embeddings.compatible import CompatibleEmbedder
    def post(*args, **kwargs):
        raise exception
    monkeypatch.setattr(CompatibleEmbedder, "_post", lambda self, *a, **kw: post(*a, **kw))
    with pytest.raises(ModelUnavailable) as error:
        CompatibleEmbedder("https://example.com/v1", "test-key", dimensions=64).embed(["text"])
    assert error_feedback(error.value)["code"] == code
    assert error_feedback(error.value)["retryable"]


def test_interruption_records_resumable_state(tmp_path):
    root = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt):
        with EvaluationCheckpoint(root, "same") as checkpoint:
            checkpoint.progress(stage="indexing", indexed_rounds=3)
            raise KeyboardInterrupt
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "interrupted" and state["indexed_rounds"] == 3


def test_offline_cli_checkpoint_resume_and_mismatch(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    path = tmp_path / "scene.json"
    path.write_text(json.dumps({"multi_session_dialogues": [{"session_id": "D1", "dialogues": [
        {"round": "D1:1", "user": "keyboard", "assistant": "blue keyboard"}]}],
        "human-annotated QAs": [{"question": "keyboard", "answer": "blue", "clue": ["D1:1"], "point": "MR"}]}), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts/evaluate_mem_gallery.py"
    output = tmp_path / "reports"
    command = [sys.executable, "-X", "utf8", str(script), "--data", str(path), "--embeddings", "none",
               "--limit", "0", "--top-k", "2", "--output-dir", str(output)]
    first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert first.returncode == 0, first.stderr
    checkpoint = next((output / "_checkpoints").iterdir())
    state = json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    second = subprocess.run([*command, "--resume", str(checkpoint)], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert second.returncode == 0, second.stderr
    assert "(checkpoint)" in second.stdout
    assert len(list(output.glob("*.json"))) == 1
    changed = command.copy()
    changed[changed.index("--top-k") + 1] = "1"
    third = subprocess.run([*changed, "--resume", str(checkpoint)], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert third.returncode != 0
    assert "checkpoint_error" in third.stderr
    assert "Traceback" not in third.stderr


def test_changed_data_or_model_identity_does_not_reuse_cache(tmp_path):
    model = Embedder()
    root = tmp_path / "run"
    with EvaluationCheckpoint(root, {"data": "original", "vision": "v1"}) as checkpoint:
        checkpoint.embedder(model).embed(["keyboard"])
    calls = len(model.calls)
    for ident in ({"data": "changed", "vision": "v1"}, {"data": "original", "vision": "v2"}):
        with pytest.raises(CheckpointError):
            with EvaluationCheckpoint(root, ident, resume=True):
                pytest.fail("changed configuration must fail before models")
    assert len(model.calls) == calls


def test_api_errors_use_shared_safe_feedback(tmp_path):
    from fastapi.testclient import TestClient
    from memory_app.main import create_app
    from memory_app.config import Settings
    model = Embedder()
    model.fail_documents = True
    with TestClient(create_app(Settings(data_dir=tmp_path, embeddings="none"), embedder=model)) as client:
        response = client.post("/add", json={"user_id": "u", "request_id": "r", "session_id": "s",
                                             "messages": [{"role": "user", "content": "text"}]})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "timeout"
        assert "secret" not in response.text


def test_cli_feedback_is_concise_and_safe(capsys):
    from memory_app.errors import cli_entrypoint
    def fail():
        raise ModelUnavailable("private-body", code="invalid_response", component="embedding")
    with pytest.raises(SystemExit) as error:
        cli_entrypoint(fail)
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert "invalid_response" in output and "private-body" not in output and "Traceback" not in output


def test_model_cache_keeps_query_and_document_roles_separate(tmp_path):
    model = Embedder()
    with EvaluationCheckpoint(tmp_path / "run", "same") as checkpoint:
        cached = checkpoint.embedder(model)
        result = cached.embed(["keyboard", "keyboard"])
        assert len(result) == 2 and len(model.calls) == 1
        assert model.calls[0][0] == ["keyboard"]
        cached.embed(["keyboard"], query=True)
        cached.embed(["keyboard"])
        assert len(model.calls) == 2
