"""Deterministic checks for the diagnostic tool; no real Ollama needed."""
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "evaluate_mem_gallery", Path(__file__).resolve().parents[1] / "scripts/evaluate_mem_gallery.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


@pytest.fixture
def scenario(tmp_path):
    data = {"character_profile": {"persona_summary": "PRIVATE_PERSONA"},
            "multi_session_dialogues": [{"session_id": "D1", "date": "2024-01-01", "dialogues": [
                {"round": "D1:1", "user": "blue keyboard", "assistant": "keyboard preferences",
                 "image_id": ["img1"], "image_caption": ["CAPTION_ONLY"]},
                {"round": "D1:2", "user": "red machine", "assistant": "stop machine"}]}],
            "human-annotated QAs": [
                {"point": "MR", "question": "blue keyboard", "answer": "ANSWER_ONLY", "clue": ["D1:1"]},
                {"point": "AR", "question": "UNKNOWN_QUESTION", "answer": "Not mentioned", "clue": []}]}
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_history_labels_and_caption_mode_are_separate(scenario):
    rounds, cases = evaluation.load_scenario(scenario)
    text = json.dumps(rounds)
    for forbidden in ("ANSWER_ONLY", "PRIVATE_PERSONA", "UNKNOWN_QUESTION", "CAPTION_ONLY"):
        assert forbidden not in text
    assert cases[0]["clues"] == ["D1:1"]
    enriched, _ = evaluation.load_scenario(scenario, include_captions=True)
    assert "CAPTION_ONLY" in enriched[0]["messages"][-1]["content"]


def test_invalid_clue_stops_evaluation(scenario):
    data = json.loads(scenario.read_text())
    data["human-annotated QAs"][0]["clue"] = ["D99:1"]
    scenario.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unknown rounds"):
        evaluation.load_scenario(scenario)


def test_round_metrics_deduplicate_and_do_not_score_empty_gold():
    metrics = evaluation.rank_metrics(["D1:1", "D1:1", "D1:2"], ["D1:1", "D1:3"])
    assert metrics["recall_at_k"] == 0.5
    assert metrics["hit_at_k"] == 1
    assert metrics["all_evidence_found"] == 0
    assert evaluation.rank_metrics(["D1:1"], []) is None
    assert evaluation.rank_metrics(["wrong", "right"], ["right"])["mrr_at_k"] == 0.5


def test_offline_evaluation_and_report(scenario):
    rounds, cases = evaluation.load_scenario(scenario)
    report = evaluation.evaluate(rounds, cases, top_k=2)
    assert set(report["summary"]) == {"lexical"}
    assert report["summary"]["lexical"]["scored_questions"] == 1
    assert report["summary"]["lexical"]["recall_at_k"] == 1
    assert report["no_clue_questions"] == 1
    assert report["indexed_messages"] == 4
    report["metadata"] = {"test": True}
    assert "D1:1" in evaluation.markdown_report(report)


def test_dense_fusion_use_real_components_but_fake_model(scenario):
    class Embedder:
        version = "test-model"

        def embed(self, texts, *, query=False):
            assert all("ANSWER_ONLY" not in text for text in texts)
            return [[1.0, 0.0] if "keyboard" in text else [0.0, 1.0] for text in texts]

    rounds, cases = evaluation.load_scenario(scenario)
    report = evaluation.evaluate(rounds, cases, Embedder(), top_k=2)
    assert set(report["summary"]) == {"lexical", "dense", "fusion"}
    for summary in report["summary"].values():
        assert summary["hit_at_k"] == 1
    for name in ("lexical", "dense", "fusion"):
        assert report["details"][0]["channels"][name]["returned"][0]["round"] == "D1:1"


@pytest.fixture
def multimodal_scene(tmp_path):
    from PIL import Image
    dialog = tmp_path / "data/dialog"
    images = tmp_path / "data/image/scene"
    dialog.mkdir(parents=True)
    images.mkdir(parents=True)
    # Deliberately mismatched extension seen in the real dataset.
    Image.new("RGB", (12, 12), "red").save(images / "one.jpg", format="PNG")
    data = {"multi_session_dialogues": [{"session_id": "D1", "date": "2024-01-01", "dialogues": [
        {"round": "D1:1", "user": "blue keyboard", "assistant": "attached photograph",
         "input_image": ["../image/scene/one.jpg"], "image_id": ["D1:IMG_001"], "image_caption": ["CAPTION_ONLY"]}]}],
        "human-annotated QAs": [{"question": "M42", "answer": "D1:IMG_001", "clue": ["D1:1"], "point": "VS"}]}
    path = dialog / "scene.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class ControlledOCR:
    version = "test-ocr"

    def analyze(self, raw, mime):
        from memory_app.vision.base import ImageAnalysis
        assert raw.startswith(b"\x89PNG") and mime == "image/png"
        return ImageAnalysis(visible_text="M42")


def test_raw_images_ocr_report_has_real_sources_and_no_base64(multimodal_scene, tmp_path):
    rounds, cases = evaluation.load_scenario(multimodal_scene, with_images=True, assets_dir=tmp_path / "report/assets")
    assert isinstance(rounds[0]["messages"][1]["content"], list)
    assert "CAPTION_ONLY" not in json.dumps(rounds)
    report = evaluation.evaluate(rounds, cases, vision=ControlledOCR())
    detail = report["details"][0]
    returned = detail["channels"]["lexical"]["returned"]
    assert returned[0]["role"] == "assistant"
    assert returned[0]["image_ids"] == ["D1:IMG_001"]
    assert detail["channels"]["lexical"]["image_metrics"]["hit_at_k"] == 1
    assert report["image_summary"]["lexical"]["recall_at_k"] == 1
    assert report["indexed_images"] == 1
    assert detail["expected_image_basis"] == "answer_mentions"
    assert detail["expected_images"][0]["image"].endswith(".png")
    assert "base64" not in json.dumps(report)
    assert (tmp_path / "report" / detail["expected_images"][0]["image"]).is_file()


def test_round_hit_is_not_image_hit(multimodal_scene, tmp_path):
    rounds, cases = evaluation.load_scenario(multimodal_scene, with_images=True, assets_dir=tmp_path / "assets")
    cases[0]["question"] = "blue keyboard"
    report = evaluation.evaluate(rounds, cases, top_k=1)
    channel = report["details"][0]["channels"]["lexical"]
    assert channel["returned"][0]["role"] == "user"
    assert channel["metrics"]["hit_at_k"] == 1
    assert channel["image_metrics"]["hit_at_k"] == 0
    assert channel["returned"][0]["images"] == []


def test_image_quota_is_applied_before_scoring(multimodal_scene, tmp_path, monkeypatch):
    from memory_app.media.local import LocalMediaStore
    rounds, cases = evaluation.load_scenario(multimodal_scene, with_images=True, assets_dir=tmp_path / "assets")
    monkeypatch.setattr(LocalMediaStore, "size", lambda *args: 31 * 1024 * 1024)
    report = evaluation.evaluate(rounds, cases, vision=ControlledOCR())
    channel = report["details"][0]["channels"]["lexical"]
    assert channel["returned"] == []
    assert channel["image_metrics"]["hit_at_k"] == 0


def test_missing_images_and_mixed_caption_mode_are_rejected(multimodal_scene, tmp_path):
    with pytest.raises(ValueError, match="excludes dataset captions"):
        evaluation.load_scenario(multimodal_scene, include_captions=True, with_images=True, assets_dir=tmp_path / "assets")
    (tmp_path / "data/image/scene/one.jpg").unlink()
    with pytest.raises(FileNotFoundError, match="download_mem_gallery_images"):
        evaluation.load_scenario(multimodal_scene, with_images=True, assets_dir=tmp_path / "assets")


@pytest.mark.parametrize("reference", ["../../private.png", "C:/private.png", "..\\private.png"])
def test_dataset_image_paths_cannot_escape(multimodal_scene, reference):
    from memory_app.evaluation.gallery_media import image_reference
    with pytest.raises(ValueError):
        image_reference(multimodal_scene, reference)


def test_query_image_is_transient_search_input(multimodal_scene, tmp_path, monkeypatch):
    from PIL import Image
    from memory_app.media.local import LocalMediaStore

    # Different bytes than history: cannot succeed merely by image hash identity.
    Image.new("RGB", (12, 12), "blue").save(tmp_path / "data/image/scene/query.png")
    data = json.loads(multimodal_scene.read_text())
    qa = data["human-annotated QAs"][0]
    qa.update(question="What is this?", question_image="../image/scene/query.png",
              image_caption="GOLD_CAPTION_DO_NOT_USE", answer="SECRET_ANSWER")
    multimodal_scene.write_text(json.dumps(data))
    rounds, cases = evaluation.load_scenario(multimodal_scene, with_images=True, assets_dir=tmp_path / "assets")
    puts = []
    original = LocalMediaStore.put

    def put(self, key, raw):
        puts.append(raw)
        return original(self, key, raw)

    monkeypatch.setattr(LocalMediaStore, "put", put)
    report = evaluation.evaluate(rounds, cases, top_k=1, vision=ControlledOCR())
    detail = report["details"][0]
    assert detail["query_image_status"] == "analyzed"
    assert detail["retrieval_query"] == "What is this?\nM42"
    assert detail["channels"]["lexical"]["returned"][0]["role"] == "assistant"
    assert report["indexed_messages"] == 2 and len(puts) == 1
    assert "query_parts" not in detail and "base64" not in json.dumps(report)
    assert "GOLD_CAPTION_DO_NOT_USE" not in detail["retrieval_query"]
    assert "SECRET_ANSWER" not in detail["retrieval_query"]
    assert (tmp_path / detail["query_images"][0]["image"]).is_file()
    # The same generic question, without the query image, has no keyword match.
    _, text_cases = evaluation.load_scenario(multimodal_scene)
    assert text_cases[0]["query_image_status"] == "omitted_text_only"
    text_report = evaluation.evaluate(rounds, text_cases, top_k=1, vision=ControlledOCR())
    assert text_report["details"][0]["channels"]["lexical"]["returned"] == []


def test_missing_query_image_is_rejected_before_evaluation(multimodal_scene, tmp_path):
    data = json.loads(multimodal_scene.read_text())
    data["human-annotated QAs"][0]["question_image"] = "../image/scene/missing.png"
    multimodal_scene.write_text(json.dumps(data))
    with pytest.raises(FileNotFoundError, match="Missing query image"):
        evaluation.load_scenario(multimodal_scene, with_images=True, assets_dir=tmp_path / "assets")


def test_downloader_includes_question_images(multimodal_scene):
    spec = importlib.util.spec_from_file_location("gallery_downloader", Path(__file__).resolve().parents[1] / "scripts/download_mem_gallery_images.py")
    downloader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(downloader)
    data = json.loads(multimodal_scene.read_text())
    data["human-annotated QAs"][0]["question_image"] = "../image/scene/query.png"
    assert set(downloader.referenced_images(data, multimodal_scene)) == {"image/scene/one.jpg", "image/scene/query.png"}


def test_query_image_processing_does_not_write_memory_or_media(multimodal_scene, tmp_path, monkeypatch):
    from memory_app.evaluation.gallery_media import image_uri
    from memory_app.memory.query import describe_query_images
    from memory_app.storage.sqlite import SQLiteStore
    from memory_app.media.local import LocalMediaStore
    store, media = SQLiteStore(tmp_path / "query.sqlite3"), LocalMediaStore(tmp_path / "media")
    def no_put(*args):
        pytest.fail("Query processing must not store media")
    monkeypatch.setattr(media, "put", no_put)
    uri, _ = image_uri(tmp_path / "data/image/scene/one.jpg")
    text, keys, artifacts = describe_query_images(store, media, ControlledOCR(), "test", [
        {"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": uri}}])
    assert "M42" in text and keys and artifacts
    with store.connect() as db:
        for table in ("units", "requests", "vectors", "image_analyses"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
