import hashlib
import importlib
import json
from pathlib import Path

import pytest

from memory_app.evaluation.checkpoint import EvaluationCheckpoint, digest
from memory_app.memory.models import Candidate
from memory_app.retrieval.fusion import rrf
from test_checkpoint import Embedder
from test_mem_gallery_evaluation import evaluation, scenario


def test_weighted_rrf_defaults_zero_weights_and_duplicate_ids():
    a, b = Candidate("a", 99), Candidate("b", -99)
    assert rrf([[a, b], [b, a]]) == rrf([[a, b], [b, a]], weights=[1, 1])
    assert rrf([[a, b], [b, a]], weights=[0.25, 1])[0].id == "b"
    assert [c.id for c in rrf([[a], [b]], weights=[0, 1])] == ["b"]
    assert rrf([[a, a]])[0].score == 1 / 61


@pytest.mark.parametrize("weights", [[-1, 1], [float('nan'), 1], [float('inf'), 1], [0, 0], [1], [True, 1]])
def test_bad_rrf_weights_rejected(weights):
    with pytest.raises(ValueError):
        rrf([[], []], weights=weights)


@pytest.fixture
def offline_report(scenario, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("compare_rrf")
    model = Embedder()
    rounds, cases = evaluation.load_scenario(scenario)
    root, path = tmp_path / "checkpoint", tmp_path / "source.json"
    with EvaluationCheckpoint(root, "offline-test") as checkpoint:
        report = evaluation.evaluate(rounds, cases, model, checkpoint=checkpoint)
        checkpoint.progress(status="completed", report=str(path.resolve()))
    report["metadata"] = {"query_protocol": "query-images-v1", "embedding_version": model.version,
        "checkpoint": str(root), "dataset": str(scenario), "dataset_sha256": hashlib.sha256(scenario.read_bytes()).hexdigest(),
        "scenario": "test", "created_at_utc": "test", "tested_questions": 2, "total_questions": 2,
        "include_dataset_captions": False}
    path.write_text(json.dumps(report), encoding="utf-8")
    def fail_network(*args, **kwargs):
        pytest.fail("Offline comparison must never call a model or network")
    monkeypatch.setattr(model, "embed", fail_network)
    import httpx
    monkeypatch.setattr(httpx, "post", fail_network)
    monkeypatch.setattr(httpx.Client, "send", fail_network)
    import httpx2
    monkeypatch.setattr(httpx2.Client, "send", fail_network)
    return module, path, root


def test_offline_comparison_replays_full_depth_without_mutating_source(offline_report):
    module, path, root = offline_report
    sources = [path, root / "state.json", root / "cache.sqlite3", root / "memory.sqlite3"]
    before = {p: p.read_bytes() for p in sources}
    result = module.compare(path)
    assert result["metadata"]["baseline_verified"] is True
    assert result["metadata"]["model_calls"] == 0
    assert "RRF 0.25:1" in result["summary"]
    assert result["summary"]["fusion"] == json.loads(path.read_text())["summary"]["fusion"]
    assert result["details"][0]["candidate_rankings"]["dense"]
    assert result["details"][1]["channels"]["fusion"]["metrics"] is None
    assert all(p.read_bytes() == before[p] for p in sources)
    with module.ReadOnlyStore(root / "memory.sqlite3").connect() as conn:
        import sqlite3
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM units")


def test_missing_query_cache_fails_without_network(offline_report):
    import sqlite3
    module, path, root = offline_report
    report = json.loads(path.read_text())
    key = digest([report["metadata"]["embedding_version"], True, report["details"][0]["retrieval_query"]])
    with sqlite3.connect(root / "cache.sqlite3") as conn:
        conn.execute("DELETE FROM model_cache WHERE key=?", (key,))
    with pytest.raises(ValueError, match="cache missing"):
        module.compare(path)


def test_mismatched_baseline_stops_comparison(offline_report):
    module, path, _ = offline_report
    report = json.loads(path.read_text())
    report["details"][0]["channels"]["dense"]["returned"][0]["memory_id"] = "wrong"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="baseline differs"):
        module.compare(path)
