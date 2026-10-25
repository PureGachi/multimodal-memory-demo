import json
from pathlib import Path
import shutil
import time

from fastapi.testclient import TestClient
import pytest

from memory_app.lab.app import create_lab


@pytest.fixture
def lab(tmp_path):
    dialog = tmp_path / "data/benchmarks/mem-gallery/data/dialog"
    dialog.mkdir(parents=True)
    data = {"multi_session_dialogues": [{"session_id": "D1", "date": "2024-01-01", "dialogues": [
        {"round": "D1:1", "user": "blue keyboard", "assistant": "I like blue keyboards"}]}],
        "human-annotated QAs": [{"question": "blue keyboard", "clue": ["D1:1"],
                                  "answer": "blue", "point": "MR"}]}
    (dialog / "example.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/evaluate_mem_gallery.py",
                    tmp_path / "scripts/evaluate_mem_gallery.py")
    return create_lab(tmp_path)


def test_viewer_and_scenarios_do_not_require_ollama(lab):
    with TestClient(lab) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "检索实验台" in response.text
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/scenarios").json() == ["example.json"]
        assert client.get("/api/reports").json() == []
        assert client.get("/api/job").json() is None


def test_run_rejects_cross_origin_unknown_files_and_extra_commands(lab):
    body = {"scenario": "example.json"}
    with TestClient(lab) as client:
        assert client.post("/api/run", json=body).status_code == 403
        headers = {"X-Memory-Lab": "1", "Origin": "https://untrusted.example"}
        assert client.post("/api/run", json=body, headers=headers).status_code == 403
        headers = {"X-Memory-Lab": "1"}
        assert client.post("/api/run", json={"scenario": "../example.json"}, headers=headers).status_code == 422
        assert client.post("/api/run", json={**body, "command": "something"}, headers=headers).status_code == 422
        assert client.post("/api/run", json={**body, "top_k": 0}, headers=headers).status_code == 422
        assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400


def test_report_paths_cannot_escape_report_directory(lab, tmp_path):
    (tmp_path / "private.json").write_text('{"secret":"do-not-expose"}')
    with TestClient(lab) as client:
        for name in ("../../../private.json", str(tmp_path / "private.json"), "missing.json"):
            response = client.get("/api/report", params={"id": name})
            assert response.status_code == 404
            assert "do-not-expose" not in response.text


def test_only_one_active_experiment(lab):
    runner = lab.state.runner
    runner.job = {"id": "existing", "status": "running", "logs": []}
    with TestClient(lab) as client:
        response = client.post("/api/run", json={"scenario": "example.json"}, headers={"X-Memory-Lab": "1"})
        assert response.status_code == 409


def test_remote_embedding_requires_explicit_consent(lab):
    from memory_app.lab.app import RunRequest
    with TestClient(lab) as client:
        response = client.post("/api/run", json={"scenario": "example.json", "embeddings": "compatible"},
                               headers={"X-Memory-Lab": "1"})
        assert response.status_code == 422
        assert client.get("/api/job").json() is None
    request = RunRequest(scenario="example.json", embeddings="compatible", allow_remote_embeddings=True)
    assert request.embeddings == "compatible"


def test_checkpoint_status_is_separate_from_reports_and_hides_raw_errors(lab, tmp_path):
    directory = tmp_path / "data/evaluations/mem-gallery/_checkpoints/test-run"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps({"protocol": "gallery-checkpoint-v1",
        "status": "failed", "stage": "indexing", "indexed_rounds": 4, "completed_questions": 0,
        "round_id": "D1:5", "options": {"scenario": "example.json"},
        "error": {"code": "timeout", "message": "private-secret"}}), encoding="utf-8")
    with TestClient(lab) as client:
        response = client.get("/api/checkpoints")
        assert response.status_code == 200
        assert "private-secret" not in response.text
        assert response.json()[0]["indexed_rounds"] == 4
        assert response.json()[0]["error"]["code"] == "timeout"
        assert client.get("/api/reports").json() == []


def test_real_offline_child_process_produces_viewable_report(lab):
    with TestClient(lab) as client:
        response = client.post("/api/run", json={"scenario": "example.json", "embeddings": "none"},
                               headers={"X-Memory-Lab": "1", "Origin": "http://testserver"})
        assert response.status_code == 202
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = client.get("/api/job").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "completed", job
        reports = client.get("/api/reports").json()
        assert len(reports) == 1 and reports[0]["id"] == job["report_id"]
        report = client.get("/api/report", params={"id": job["report_id"]}).json()
        assert report["summary"]["lexical"]["hit_at_k"] == 1
        assert report["details"][0]["channels"]["lexical"]["returned"][0]["content"]


def test_child_failure_is_visible_and_another_run_can_start(lab, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("private internals")

    monkeypatch.setattr("memory_app.lab.app.subprocess.Popen", fail)
    with TestClient(lab) as client:
        for _ in range(2):
            assert client.post("/api/run", json={"scenario": "example.json"},
                               headers={"X-Memory-Lab": "1"}).status_code == 202
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = client.get("/api/job").json()
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            assert job["status"] == "failed"
            assert "private internals" not in str(job)


def make_image_report(tmp_path, kind="ocr"):
    from PIL import Image
    folder = tmp_path / "data/evaluations" / kind / "sample"
    folder.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(folder / "photo.png")
    report = {"created_at_utc": "2026-10-13T12:00:00+00:00", "samples": [
        {"name": "photo", "image": "photo.png", "unit_id": "u1", "query": "M42",
         "visible_text": "M42", "returned_ids": ["u1"]}]}
    if kind == "images":
        report = {"image": "photo.png", "image_artifacts": [{"visible_text": "M42"}],
                  "query": "M42", "retrieved": []}
    (folder / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return folder, report


@pytest.mark.parametrize("kind", ["ocr", "images"])
def test_image_reports_are_listed_and_can_serve_referenced_images(lab, tmp_path, kind):
    folder, report = make_image_report(tmp_path, kind)
    with TestClient(lab) as client:
        entries = client.get("/api/reports").json()
        assert len(entries) == 1 and entries[0]["kind"] == kind
        identity = entries[0]["id"]
        data = client.get("/api/report", params={"id": identity}).json()
        assert data["report_type"] == ("ocr" if kind == "ocr" else "image-inspection")
        image = client.get("/api/report-image", params={"id": identity, "image": "photo.png"})
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert image.headers["x-content-type-options"] == "nosniff"
        assert image.content == (folder / "photo.png").read_bytes()


def test_report_images_cannot_escape_directory_or_load_unlisted_files(lab, tmp_path):
    folder, report = make_image_report(tmp_path)
    (folder / "unlisted.png").write_bytes((folder / "photo.png").read_bytes())
    report["images"] = ["../../../private.png", "C:/private.png", "https://example.com/photo.png", "/private.png", "..\\private.png"]
    (folder / "report.json").write_text(json.dumps(report))
    with TestClient(lab) as client:
        for name in [*report["images"], "unlisted.png", "missing.png"]:
            result = client.get("/api/report-image", params={"id": "ocr/sample/report.json", "image": name})
            assert result.status_code == 404
        assert client.get("/api/report", params={"id": "ocr/../../../private.json"}).status_code == 404


def test_fake_raster_and_oversize_images_are_rejected(lab, tmp_path):
    folder, report = make_image_report(tmp_path)
    (folder / "photo.png").write_text('<svg onload="alert(1)"></svg>')
    with TestClient(lab) as client:
        assert client.get("/api/report-image", params={"id": "ocr/sample/report.json", "image": "photo.png"}).status_code == 404
        with (folder / "photo.png").open("wb") as stream:
            stream.truncate(10 * 1024 * 1024 + 1)
        assert client.get("/api/report-image", params={"id": "ocr/sample/report.json", "image": "photo.png"}).status_code == 404


def test_image_report_catalog_ignores_incomplete_and_invalid_reports(lab, tmp_path):
    folder, _ = make_image_report(tmp_path)
    for name, text in [("incomplete.json", "{}"), ("invalid.json", "bad-json"), ("list.json", "[]")]:
        (folder / name).write_text(text)
    with TestClient(lab) as client:
        assert len(client.get("/api/reports").json()) == 1


def test_missing_old_inspection_image_does_not_read_absolute_path(lab, tmp_path):
    folder, report = make_image_report(tmp_path, "images")
    report["image"] = str((folder / "photo.png").resolve())
    (folder / "report.json").write_text(json.dumps(report))
    with TestClient(lab) as client:
        result = client.get("/api/report-image", params={"id": "images/sample/report.json", "image": report["image"]})
        assert result.status_code == 404


def test_raw_image_run_options_are_safe_and_captions_exclusive(lab):
    runner = lab.state.runner
    runner.job = {"id": "existing", "status": "running", "logs": []}
    with TestClient(lab) as client:
        base = {"scenario": "example.json", "with_images": True}
        headers = {"X-Memory-Lab": "1"}
        assert client.post("/api/run", json={**base, "include_captions": True}, headers=headers).status_code == 422
        assert client.post("/api/run", json={**base, "ocr_languages": "anything --command"}, headers=headers).status_code == 422
        assert client.post("/api/run", json={**base, "ocr_languages": "eng"}, headers=headers).status_code == 409
