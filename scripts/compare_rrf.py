"""Offline RRF comparison. Read-only checkpoint access, no model clients or .env."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import uuid

from memory_app.evaluation.checkpoint import digest, atomic_json
from memory_app.errors import cli_entrypoint
from memory_app.media.local import LocalMediaStore
from memory_app.memory.evidence import compose
from memory_app.memory.indexing import retrieval_text
from memory_app.memory.models import Unit, Candidate
from memory_app.memory.units import text_chunks
from memory_app.retrieval.fusion import rrf
from memory_app.retrieval.lexical import lexical_search
from memory_app.vector_stores.sqlite import SQLiteVectorStore
from memory_app.evaluation.metrics import rank_metrics, summarize, summarize_images
from memory_app.evaluation.reporting import markdown_report


class ReadOnlyStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ValueError("Required checkpoint database is missing")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            yield conn
        finally:
            conn.close()


class ReadOnlyMedia(LocalMediaStore):
    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_dir():
            raise ValueError("Checkpoint media directory is missing")

    def put(self, *args):
        raise RuntimeError("Offline comparison cannot write media")


def cached_query(cache, version, text):
    result = []
    for chunk in text_chunks(text):
        key = digest([version, True, chunk])
        with cache.connect() as conn:
            row = conn.execute("SELECT payload FROM model_cache WHERE kind='embedding' AND key=?", (key,)).fetchone()
        if row is None:
            raise ValueError("Query vector cache missing; offline comparison will not call a model")
        vector = json.loads(row[0])
        if (not isinstance(vector, list) or not vector or any(type(x) not in (int, float)
                or not math.isfinite(x) for x in vector) or not 0 < math.hypot(*vector) < math.inf):
            raise ValueError("Invalid cached query vector")
        result.append(vector)
    return result


def compare(report_path):
    report_path = Path(report_path).resolve()
    original = json.loads(report_path.read_text(encoding="utf-8-sig"))
    meta = original["metadata"]
    if meta.get("query_protocol") != "query-images-v1" or not meta.get("embedding_version"):
        raise ValueError("Requires a completed query-images-v1 dense evaluation report")
    root = Path(meta["checkpoint"])
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    if state.get("status") != "completed" or Path(state.get("report", "")).resolve() != report_path:
        raise ValueError("Checkpoint is not completed or belongs to a different report")
    dataset = Path(meta["dataset"])
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != meta["dataset_sha256"]:
        raise ValueError("Dataset changed since the original evaluation")
    data = json.loads(dataset.read_text(encoding="utf-8-sig"))
    turns = {t["round"]: t for s in data["multi_session_dialogues"] for t in s["dialogues"]}
    store, cache = ReadOnlyStore(root / "memory.sqlite3"), ReadOnlyStore(root / "cache.sqlite3")
    with store.connect() as conn:
        units = [Unit(**json.loads(row[0])) for row in conn.execute(
            "SELECT payload FROM units WHERE user_id=? ORDER BY id", ("mem-gallery-eval",))]
    source = {str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
        ["mem-gallery-eval", round_id, order], ensure_ascii=False))): round_id
        for round_id in turns for order in (0, 1)}
    if len(units) != original["indexed_messages"] or any(u.id not in source for u in units):
        raise ValueError("Checkpoint history differs from the source report")
    media, vectors = ReadOnlyMedia(root / "media"), SQLiteVectorStore(store)
    assets = report_path.parent / (report_path.stem + "-assets")
    files = {p.stem: p for p in assets.iterdir() if p.suffix in {".png", ".jpg", ".webp"}} if assets.is_dir() else {}
    def record(unit):
        turn = turns[source[unit.id]]
        images = []
        for key in unit.media_keys:
            asset = files.get(key.rsplit("/", 1)[-1])
            if asset is None:
                raise ValueError("Source report image asset is missing")
            images.append(asset.relative_to(report_path.parent).as_posix())
        return {"memory_id": unit.id, "round": source[unit.id], "role": unit.role, "content": unit.text,
                "images": images, "image_ids": turn.get("image_id", []) if unit.media_keys else [],
                "image_artifacts": unit.image_artifacts,
                "image_preprocessing": [p for p in original.get("history_image_preprocessing", [])
                    if p["image_id"] in turn.get("image_id", [])] if unit.media_keys else []}
    records = {u.id: record(u) for u in units}
    docs = [replace(u, text=retrieval_text(u)) for u in units]
    result = deepcopy(original)
    result["index_seconds"] = 0
    result["details"] = []
    depth, top_k = original["candidate_depth"], original["top_k"]
    names = ["lexical", "dense", "fusion", "RRF 0.25:1", "RRF 0.5:1"]
    for detail in original["details"]:
        text = detail["retrieval_query"]
        lexical = lexical_search(docs, text, depth)
        qvecs = cached_query(cache, meta["embedding_version"], text)
        dense = vectors.search("mem-gallery-eval", meta["embedding_version"], qvecs, depth)
        keys = {a["media_key"] for a in detail.get("query_image_artifacts", [])}
        identity = [Candidate(u.id, 1) for u in units if keys.intersection(u.media_keys)]
        lists = [lexical, identity, dense] if keys else [lexical, dense]
        rankings = {"lexical": lexical, "dense": dense, "fusion": rrf(lists)}
        for weight in (0.25, 0.5):
            rankings[f"RRF {weight}:1"] = rrf(lists, weights=[weight, 1, 1] if keys else [weight, 1])
        current = deepcopy(detail)
        current["channels"] = {}
        current["candidate_rankings"] = {name: [{"id": c.id, "score": c.score} for c in candidates]
            for name, candidates in {"lexical": lexical, "identity": identity, "dense": dense}.items()}
        expected = [i["image_id"] for i in detail.get("expected_images", [])]
        for name, candidates in rankings.items():
            selected = compose(candidates, units, media, top_k)["data"]
            returned = [{"rank": i, "score": item["score"], **records[item["id"]]} for i, item in enumerate(selected, 1)]
            if name in ("lexical", "dense", "fusion"):
                if [r["memory_id"] for r in returned] != [r["memory_id"] for r in detail["channels"][name]["returned"]]:
                    raise ValueError("Replayed baseline differs; refusing a misleading weight comparison")
            current["channels"][name] = {"returned": returned, "latency_ms": 0,
                "metrics": rank_metrics([r["round"] for r in returned], detail["clues"]),
                "image_metrics": rank_metrics([i for r in returned for i in r["image_ids"]], expected)}
        result["details"].append(current)
    result["summary"] = summarize(result["details"], names)
    result["image_summary"] = summarize_images(result["details"], names)
    result["by_point"] = {p: summarize([d for d in result["details"] if d["point"] == p], names)
        for p in sorted({d["point"] for d in result["details"]})}
    result["weight_changes"] = {}
    for name in names[3:]:
        changes = []
        for detail in result["details"]:
            old, new = detail["channels"]["fusion"]["metrics"], detail["channels"][name]["metrics"]
            if old is not None and any(old[k] != new[k] for k in ("hit_at_k", "recall_at_k", "mrr_at_k", "all_evidence_found")):
                changes.append({"question": detail["number"], "point": detail["point"], "before": old, "after": new})
        result["weight_changes"][name] = changes
    result["metadata"].update(created_at_utc=datetime.now(timezone.utc).isoformat(),
        scenario=meta["scenario"] + " · 离线 RRF 权重对比", experiment="offline-rrf-weights-v1",
        source_report=str(report_path), source_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        weight_policy="lexical:dense = 0.25:1, 0.5:1, 1:1; identity fixed at 1; dense-only control has no identity",
        validation_scope="exploratory tuning on the same scene, not held-out validation",
        latency_policy="offline replay; channel latency_ms=0 is a placeholder, not measured latency",
        index_timing="no indexing in this experiment; read-only replay of source checkpoint",
        baseline_verified=True, model_calls=0)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="Completed source JSON report")
    args = parser.parse_args()
    result = compare(args.report)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-rrf-" + uuid.uuid4().hex[:8]
    output = args.report.resolve().parent / name
    atomic_json(output.with_suffix(".json"), result)
    text = markdown_report(result)
    text += "\n## 权重变化逐题对照（相对等权 RRF）\n\n仅作同场景探索，不是验证集结论；通道耗时为占位值，不可比较。\n"
    for name, changes in result["weight_changes"].items():
        text += f"\n### {name}\n\n| 题号 | 题型 | Hit 前→后 | Recall 前→后 | MRR 前→后 |\n| --- | --- | --- | --- | --- |\n"
        for change in changes:
            a, b = change["before"], change["after"]
            text += f"| Q{change['question']} | {change['point']} | {a['hit_at_k']}→{b['hit_at_k']} | {a['recall_at_k']:.3f}→{b['recall_at_k']:.3f} | {a['mrr_at_k']:.3f}→{b['mrr_at_k']:.3f} |\n"
    output.with_suffix(".md").write_text(text, encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("Report:", output.with_suffix(".md"))


if __name__ == "__main__":
    cli_entrypoint(main)
