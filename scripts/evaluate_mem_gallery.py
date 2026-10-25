"""Local retrieval diagnosis using Mem-Gallery clues, NOT official QA scoring."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from time import perf_counter
import uuid
import re
import sys
from dataclasses import replace
from contextlib import nullcontext, closing

from memory_app.config import Settings
from memory_app.embeddings.factory import build_embedder
from memory_app.vision.factory import build_vision
from memory_app.evaluation.checkpoint import EvaluationCheckpoint, atomic_json
from memory_app.errors import cli_entrypoint
from memory_app.media.local import LocalMediaStore
from memory_app.memory.add import add_memory
from memory_app.memory.query import describe_query_images
from memory_app.memory.models import Candidate
from memory_app.memory.units import text_chunks
from memory_app.memory.indexing import retrieval_text
from memory_app.memory.evidence import compose
from memory_app.evaluation.gallery_media import image_reference, prepared_image_uri, COMPRESSION_POLICY
from memory_app.vision.ocr import TesseractAnalyzer
from memory_app.vision.pipeline import ImagePipeline
from memory_app.retrieval.fusion import rrf
from memory_app.retrieval.lexical import lexical_search
from memory_app.storage.sqlite import SQLiteStore
from memory_app.vector_stores.sqlite import SQLiteVectorStore


DEFAULT_DATA = Path("data/benchmarks/mem-gallery/data/dialog/AI_Robotics_Automation_Future_Tech.json")


def query_image(path, reference, assets_dir, *, compress_oversize=False):
    target, _ = image_reference(path, reference)
    if not target.is_file():
        raise FileNotFoundError(f"Missing query image: {reference}. Run scripts/download_mem_gallery_images.py first.")
    uri, raw, preprocessing = prepared_image_uri(target, compress_oversize=compress_oversize)
    digest = hashlib.sha256(raw).hexdigest()
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[uri[5:].split(";", 1)[0]]
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    asset = assets_dir / (digest + extension)
    if not asset.exists():
        asset.write_bytes(raw)
    return {"image": assets_dir.name + "/" + asset.name, "sha256": digest,
            **({"preprocessing": preprocessing} if compress_oversize else {})}, {"type": "image_url", "image_url": {"url": uri}}


def load_scenario(path, include_captions=False, *, with_images=False, assets_dir=None, image_role="assistant", compress_oversize=False):
    """Keep history and evaluation labels separate. Never ingest QA or persona."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if with_images and (assets_dir is None or include_captions):
        raise ValueError("Raw-image mode requires report assets directory and excludes dataset captions")
    if image_role not in {"user", "assistant"}:
        raise ValueError("Invalid image attachment role")
    rounds, seen = [], set()
    for session in data["multi_session_dialogues"]:
        for turn in session["dialogues"]:
            round_id = turn["round"]
            if not isinstance(round_id, str) or not round_id or round_id in seen:
                raise ValueError(f"Invalid or duplicate round: {round_id!r}")
            seen.add(round_id)
            messages = []
            for role in ("user", "assistant"):
                text = turn.get(role)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(f"Missing {role} text at {round_id}")
                messages.append({"role": role, "content": text})
            captions = turn.get("image_caption", [])
            if not isinstance(captions, list) or not all(isinstance(c, str) for c in captions):
                raise ValueError(f"Invalid captions at {round_id}")
            if include_captions and captions:
                messages[-1]["content"] += "\n[Dataset-provided image captions]\n" + "\n".join(captions)
            images = []
            if with_images:
                references, ids = turn.get("input_image", []), turn.get("image_id", [])
                if not isinstance(references, list) or not isinstance(ids, list) or len(references) != len(ids):
                    raise ValueError(f"Image paths and IDs do not match at {round_id}")
                parts = []
                for reference, image_id in zip(references, ids):
                    if not isinstance(image_id, str) or not image_id:
                        raise ValueError(f"Invalid image ID at {round_id}")
                    target, _ = image_reference(path, reference)
                    if not target.is_file():
                        raise FileNotFoundError(f"Missing dataset image: {reference}. Run scripts/download_mem_gallery_images.py first.")
                    uri, raw, preprocessing = prepared_image_uri(target, compress_oversize=compress_oversize)
                    digest = hashlib.sha256(raw).hexdigest()
                    assets_dir = Path(assets_dir)
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[uri[5:].split(";", 1)[0]]
                    asset = assets_dir / (digest + extension)
                    if not asset.exists():
                        asset.write_bytes(raw)
                    images.append({"image_id": image_id, "image": assets_dir.name + "/" + asset.name,
                                   "sha256": digest, **({"preprocessing": preprocessing} if compress_oversize else {})})
                    parts.append({"type": "image_url", "image_url": {"url": uri}})
                if parts:
                    message = next(m for m in messages if m["role"] == image_role)
                    message["content"] = [{"type": "text", "text": message["content"]}, *parts]
            rounds.append({"round": round_id, "session": session["session_id"],
                           "date": session.get("date"), "messages": messages,
                           **({"images": images, "image_role": image_role} if with_images else {}),
                           "has_image": bool(turn.get("input_image") or turn.get("image_id"))})
    if not rounds:
        raise ValueError("Scenario has no dialogue rounds")
    cases = []
    for index, qa in enumerate(data["human-annotated QAs"], 1):
        question, clues = qa["question"], qa["clue"]
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Invalid question {index}")
        if not isinstance(clues, list) or not all(isinstance(c, str) for c in clues):
            raise ValueError(f"Invalid clues for question {index}")
        unknown = set(clues) - seen
        if unknown:
            raise ValueError(f"Question {index} references unknown rounds: {sorted(unknown)}")
        cases.append({"number": index, "question": question, "point": qa.get("point", "unknown"),
                      "answer": qa.get("answer"), "clues": sorted(set(clues))})
        reference = qa.get("question_image")
        if reference:
            image_reference(path, reference)  # Validate even in text-only mode.
        case = cases[-1]
        case["has_query_image"] = bool(reference)
        case["query_images"] = []
        case["query_image_status"] = "omitted_text_only" if reference else "not_present"
        if reference and with_images:
            asset, part = query_image(path, reference, assets_dir, compress_oversize=compress_oversize)
            case["query_images"] = [asset]
            case["query_parts"] = [{"type": "text", "text": question}, part]
            case["query_image_status"] = "provided"
    if not cases:
        raise ValueError("Scenario has no evaluation questions")
    return rounds, cases


def rank_metrics(round_ids, clues):
    """K is returned messages; repeated rounds count only once towards recall."""
    gold = set(clues)
    if not gold:
        return None
    found = gold.intersection(round_ids)
    first = next((i for i, value in enumerate(round_ids, 1) if value in gold), None)
    return {"hit_at_k": int(bool(found)), "recall_at_k": len(found) / len(gold),
            "mrr_at_k": 1 / first if first else 0.0,
            "first_evidence_rank": first, "all_evidence_found": int(found == gold)}


def summarize(details, names):
    result = {}
    for name in names:
        scored = [d["channels"][name]["metrics"] for d in details
                  if d["channels"][name]["metrics"] is not None]
        result[name] = {"scored_questions": len(scored)}
        for field in ("hit_at_k", "recall_at_k", "mrr_at_k", "all_evidence_found"):
            result[name][field] = sum(m[field] for m in scored) / len(scored) if scored else None
    return result


def summarize_images(details, names):
    result = {}
    for name in names:
        scored = [detail["channels"][name]["image_metrics"] for detail in details
                  if detail["channels"][name]["image_metrics"] is not None]
        result[name] = {"scored_questions": len(scored)}
        for field in ("hit_at_k", "recall_at_k", "all_evidence_found"):
            result[name][field] = sum(item[field] for item in scored) / len(scored) if scored else None
    return result


def evaluate(rounds, cases, embedder=None, top_k=5, *, vision=None, checkpoint=None):
    """Actual Add, OCR, retrieval and media-budget compose; no HTTP or final answer."""
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be between 1 and 100")
    if vision is None and any(case.get("query_parts") for case in cases):
        raise ValueError("Query images require an image analyzer")
    started = perf_counter()
    details = []
    names = ["lexical", "dense", "fusion"] if embedder else ["lexical"]
    depth = max(top_k * 3, 30)  # Same candidate depth as memory.search.
    if checkpoint:
        embedder, vision = checkpoint.embedder(embedder), checkpoint.vision(vision)
    with (nullcontext(str(checkpoint.root)) if checkpoint else tempfile.TemporaryDirectory(prefix="mem-gallery-eval-")) as folder:
        root = Path(folder)
        store = SQLiteStore(root / "memory.sqlite3")
        media, vectors = LocalMediaStore(root / "media"), SQLiteVectorStore(store)
        # Deterministic Add unit IDs recover provenance correctly even when the
        # database already contains later rounds from an earlier attempt.
        source = {str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
            ["mem-gallery-eval", turn["round"], order], ensure_ascii=False))): turn["round"]
            for turn in rounds for order, _ in enumerate(turn["messages"])}
        for i, turn in enumerate(rounds, 1):
            if checkpoint:
                checkpoint.progress(stage="indexing", round_id=turn["round"], question_number=None)
            add_memory({"user_id": "mem-gallery-eval", "request_id": turn["round"],
                        "session_id": turn["session"], "messages": turn["messages"]},
                       store, media, embedder, vectors, vision=vision)
            if checkpoint:
                checkpoint.progress(indexed_rounds=i)
            if i % 20 == 0 or i == len(rounds):
                print(f"Indexed rounds: {i}/{len(rounds)}", flush=True)
        units = store.list_units("mem-gallery-eval")
        by_id = {u.id: u for u in units}
        by_round = {turn["round"]: turn for turn in rounds}
        by_image = {image["image_id"]: image for turn in rounds for image in turn.get("images", [])}
        def record(unit):
            turn = by_round[source[unit.id]]
            images = turn.get("images", []) if unit.media_keys else []
            return {"memory_id": unit.id, "round": turn["round"], "role": unit.role,
                    "content": unit.text, "images": [i["image"] for i in images],
                    "image_ids": [i["image_id"] for i in images], "image_artifacts": unit.image_artifacts,
                    "image_preprocessing": [i["preprocessing"] for i in images if "preprocessing" in i]}
        gold_rounds = {turn["round"]: {"round": turn["round"], "date": turn["date"],
                       "has_image": turn["has_image"], "messages": [record(unit) for unit in units if source[unit.id] == turn["round"]]}
                       for turn in rounds}
        index_seconds = perf_counter() - started
        for i, case in enumerate(cases, 1):
            if checkpoint:
                checkpoint.progress(stage="questions", round_id=None, question_number=case["number"])
                saved = checkpoint.question(case["number"])
                if saved is not None:
                    details.append(saved)
                    checkpoint.progress(completed_questions=i)
                    print(f"Questions: {i}/{len(cases)} (checkpoint)", flush=True)
                    continue
            q_started = perf_counter()
            parts = case.get("query_parts", case["question"])
            query_text, query_keys, query_artifacts = describe_query_images(
                store, media, vision, "mem-gallery-eval", parts)
            analysis_ms = (perf_counter() - q_started) * 1000
            q_started = perf_counter()
            lexical = lexical_search([replace(unit, text=retrieval_text(unit)) for unit in units], query_text, depth)
            lexical_ms = (perf_counter() - q_started) * 1000
            rankings = {"lexical": lexical}
            times = {"lexical": lexical_ms}
            if embedder:
                t = perf_counter()
                qvecs = embedder.embed(text_chunks(query_text), query=True)
                dense = vectors.search("mem-gallery-eval", embedder.version, qvecs, depth)
                times["dense"] = (perf_counter() - t) * 1000
                t = perf_counter()
                identity = [Candidate(unit.id, 1) for unit in units if set(query_keys) & set(unit.media_keys)]
                rankings.update(dense=dense, fusion=rrf([lexical, identity, dense] if query_keys else [lexical, dense]))
                times["fusion"] = lexical_ms + times["dense"] + (perf_counter() - t) * 1000
            channels = {}
            # Explicit answer image IDs are the narrower reference when present;
            # otherwise display images in clue rounds as evidence, not unique answers.
            answer_ids = sorted(set(re.findall(r"D\d+:IMG_\d+", str(case.get("answer", "")))) & set(by_image))
            clue_ids = sorted({image["image_id"] for clue in case["clues"] for image in by_round[clue].get("images", [])})
            expected_ids = answer_ids or clue_ids
            for name, candidates in rankings.items():
                selected = compose(candidates, units, media, top_k)["data"]
                returned = [{"rank": rank, "score": item["score"], **record(by_id[item["id"]])}
                            for rank, item in enumerate(selected, 1)]
                channels[name] = {"metrics": rank_metrics([r["round"] for r in returned], case["clues"]),
                                  "image_metrics": rank_metrics([image for item in returned for image in item["image_ids"]], expected_ids),
                                  "latency_ms": times[name], "returned": returned}
            public_case = {key: value for key, value in case.items() if key != "query_parts"}
            details.append({**public_case, "retrieval_query": query_text,
                            "query_image_artifacts": query_artifacts, "query_analysis_ms": analysis_ms,
                            "query_image_status": "analyzed" if query_artifacts else case.get("query_image_status", "not_present"),
                            "status": "scored" if case["clues"] else "no_clue_unscored",
                            "gold_evidence": [gold_rounds[c] for c in case["clues"]], "channels": channels,
                            "expected_images": [by_image[i] for i in expected_ids],
                            "expected_image_basis": "answer_mentions" if answer_ids else "clue_rounds"})
            if checkpoint:
                checkpoint.save_question(details[-1])
                checkpoint.progress(completed_questions=i)
            print(f"Questions: {i}/{len(cases)}", flush=True)
        return {"index_seconds": index_seconds, "indexed_messages": len(units), "top_k": top_k,
                "candidate_depth": depth, "summary": summarize(details, names),
                "by_point": {point: summarize([d for d in details if d["point"] == point], names)
                             for point in sorted({d["point"] for d in details})},
                "no_clue_questions": sum(not d["clues"] for d in details), "details": details,
                "indexed_images": len(by_image),
                "history_image_preprocessing": [{"image_id": image["image_id"], **image["preprocessing"]}
                    for image in by_image.values() if "preprocessing" in image],
                "image_summary": summarize_images(details, names)}


def markdown_report(report):
    lines = ["# Mem-Gallery 本地检索诊断", "", "不是官方比赛成绩，不生成答案、不评价答案正确率。", "",
             "本实验索引所选场景的全部历史，不模拟逐会话/时间截止评测；是否处理原图见 metadata.raw_images_used。",
             "K 按消息计数；命中任一标注轮次算 Hit，找回标注轮次的比例算 Recall；MRR 只看前 K 条。",
             "同一轮的 user/assistant 都视为该轮证据，属于较宽松的轮次级评估，不保证能回答问题。",
             "无 clue 的题只展示候选、不计分；不是拒答准确率。报告中的答案只供检查，未写入记忆。", "",
             "```json", json.dumps(report["metadata"], ensure_ascii=False, indent=2), "```", "",
             f"索引耗时：{report['index_seconds']:.2f} 秒；消息数：{report['indexed_messages']}；无 clue 题数：{report['no_clue_questions']}。", "",
             "## 总体结果", "", "| 通道 | 计分题数 | Hit@K | Recall@K | MRR@K | 全部证据找回率 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for name, values in report["summary"].items():
        scores = ["—" if values[k] is None else f"{values[k]:.3f}" for k in
                  ("hit_at_k", "recall_at_k", "mrr_at_k", "all_evidence_found")]
        lines.append(f"| {name} | {values['scored_questions']} | " + " | ".join(scores) + " |")
    for detail in report["details"]:
        lines += ["", f"## Q{detail['number']} [{detail['point']}]", "", detail["question"], "",
                  f"标注轮次：{', '.join(detail['clues']) or '无 clue（不计分）'}", "",
                  "参考答案（仅用于人工检查）：", "", "```text", str(detail["answer"]), "```", "",
                  "### 标注证据原文", ""]
        lines += [f"![提问附图]({image['image']})" for image in detail.get("query_images", [])]
        lines += ["检索实际使用的文字（含临时图片线索，不写入历史）：", "```text", detail.get("retrieval_query", detail["question"]), "```", ""]
        for gold in detail["gold_evidence"]:
            lines += [f"#### {gold['round']}（{gold['date']}）", ""]
            for message in gold["messages"]:
                lines += [message["role"], "", "```text", message["content"], "```", ""]
                lines += [f"![标注证据图片（预处理情况见 metadata）]({image})" for image in message.get("images", [])]
        if detail.get("expected_images"):
            lines += ["### 参考图片", "", "依据：" + detail["expected_image_basis"], ""]
            lines += [f"![{image['image_id']}]({image['image']})" for image in detail["expected_images"]]
        for name, channel in detail["channels"].items():
            lines += [f"### {name} · {channel['latency_ms']:.1f} ms", "",
                      "指标：`" + json.dumps(channel["metrics"], ensure_ascii=False) + "`", ""]
            for item in channel["returned"]:
                mark = "命中标注轮次" if item["round"] in detail["clues"] else "非标注轮次"
                lines += [f"#### 第 {item['rank']} 条 · {item['round']} · {item['role']} · {mark}", "",
                          f"通道分数：{item['score']:.6f}", "", "```text", item["content"], "```", ""]
                lines += [f"![实际召回图片]({image})" for image in item.get("images", [])]
                for artifact in item.get("image_artifacts", []):
                    if artifact.get("description"):
                        lines += ["图片描述（视觉派生）：", "```text", artifact["description"], "```", ""]
                    lines += ["OCR（派生信息）：", "```text", artifact["visible_text"], "```", ""]
                    if artifact.get("contextual_description"):
                        lines += ["对话背景（说话者陈述，不是视觉事实）：", "```text", artifact["contextual_description"], "```", "",
                                  "背景来源消息：" + ", ".join(m["message_id"] for m in artifact.get("context_messages", [])), ""]
    return "\n".join(lines) + "\n"


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--embeddings", choices=["env", "ollama", "none", "compatible"], default="env",
                        help="env follows MEMORY_EMBEDDINGS; compatible uses paid text-embedding-v4 API")
    parser.add_argument("--include-captions", action="store_true", help="Use dataset captions, NOT our image understanding")
    parser.add_argument("--with-images", action="store_true", help="Ingest original images with real local OCR; copy report assets")
    parser.add_argument("--compress-oversize", action="store_true", help="Explicitly derive <=10 MiB JPEG copies of oversized originals; local diagnostic policy, not official adapter")
    parser.add_argument("--ocr-languages", choices=["eng", "chi_sim", "chi_sim+eng"], default="eng")
    parser.add_argument("--image-analysis", choices=["ocr", "configured"], default=None,
                        help="configured follows vision/OCR environment settings; may call paid vision API")
    parser.add_argument("--limit", type=int, default=10, help="First N questions; 0 means all. Always indexes all history.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/mem-gallery"))
    parser.add_argument("--resume", type=Path, help="Resume the exact checkpoint directory printed by a previous run; no automatic retry")
    args = parser.parse_args()
    if args.limit < 0 or not 1 <= args.top_k <= 100:
        parser.error("--limit must be >= 0; --top-k must be between 1 and 100")
    if args.with_images and args.include_captions:
        parser.error("--with-images excludes --include-captions for a clean OCR comparison")
    if args.compress_oversize and not args.with_images:
        parser.error("--compress-oversize requires --with-images")
    settings = Settings.from_env()
    if args.embeddings != "env":
        settings = replace(settings, embeddings=args.embeddings)
    settings.validate()
    analysis_mode = args.image_analysis or ("configured" if settings.profile == "competition" else "ocr")
    if settings.profile == "competition" and args.with_images and analysis_mode != "configured":
        parser.error("Competition profile requires --image-analysis configured, not local OCR")
    if args.resume:
        checkpoint_dir = args.resume.resolve()
        if checkpoint_dir.parent.name != "_checkpoints":
            parser.error("--resume must point to a run directory immediately under _checkpoints")
        name = checkpoint_dir.name
        args.output_dir = checkpoint_dir.parent.parent
    else:
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        checkpoint_dir = args.output_dir.resolve() / "_checkpoints" / name
    rounds, cases = load_scenario(args.data, args.include_captions, with_images=args.with_images,
                                  assets_dir=args.output_dir / (name + "-assets"), compress_oversize=args.compress_oversize)
    total_questions = len(cases)
    if args.limit:
        cases = cases[:args.limit]
    embedder = build_embedder(settings)
    vision = None
    if args.with_images:
        if analysis_mode == "configured":
            vision = build_vision(settings)
        else:
            executable = Path(sys.prefix) / "Library/bin/tesseract.exe"
            vision = ImagePipeline([TesseractAnalyzer(str(executable) if executable.is_file() else "tesseract", args.ocr_languages)])
    identity = {"query_protocol": "query-images-v1", "rounds": rounds, "cases": cases, "top_k": args.top_k,
                "dataset_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
                "embedding_version": embedder.version if embedder else None,
                "vision_version": vision.version if vision else None,
                "profile": settings.profile, "include_captions": args.include_captions,
                "with_images": args.with_images, "analysis_mode": analysis_mode if args.with_images else None}
    if args.compress_oversize:
        identity["image_preprocessing_policy"] = COMPRESSION_POLICY
    options = {"scenario": args.data.name, "embeddings": settings.embeddings,
               "limit": args.limit, "top_k": args.top_k, "include_captions": args.include_captions,
               "with_images": args.with_images, "image_analysis": analysis_mode, "ocr_languages": args.ocr_languages,
               "compress_oversize": args.compress_oversize}
    print(f"Checkpoint: {checkpoint_dir}", flush=True)
    print("Resume: reuse the same arguments and add --resume followed by this directory.", flush=True)
    with (closing(embedder) if hasattr(embedder, "close") else nullcontext()), (closing(vision) if hasattr(vision, "close") else nullcontext()), EvaluationCheckpoint(checkpoint_dir, identity, resume=bool(args.resume), options=options) as checkpoint:
        checkpoint.progress(total_rounds=len(rounds), total_questions=len(cases))
        report = evaluate(rounds, cases, embedder, args.top_k, vision=vision, checkpoint=checkpoint)
        checkpoint.progress(stage="export")
        export_report(report, args, settings, analysis_mode, embedder, vision, name, total_questions, cases, checkpoint)


def export_report(report, args, settings, analysis_mode, embedder, vision, name, total_questions, cases, checkpoint):
    report["metadata"] = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                          "checkpoint": str(checkpoint.root), "resumed": bool(args.resume),
                          "index_timing": "indexing time in this attempt only; saved successful model results are reused",
                          "model_profile": settings.profile, "image_analysis_mode": analysis_mode if args.with_images else None,
                          "dataset": str(args.data.resolve()), "dataset_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
                          "scenario": args.data.stem, "total_questions": total_questions, "tested_questions": len(cases),
                          "question_selection": "first N in dataset order (not a representative random sample)",
                          "history_scope": "all sessions in this scenario; no temporal cutoff",
                          "query_protocol": "query-images-v1",
                          "query_image_policy": "analyzed transiently; never added to history" if args.with_images else "omitted_text_only",
                          "query_images_analyzed": sum(bool(d.get("query_image_artifacts")) for d in report["details"]),
                          "image_preprocessing_policy": COMPRESSION_POLICY if args.compress_oversize else "original-only",
                          "transformed_history_images": sum(p["transformed"] for p in report.get("history_image_preprocessing", [])),
                          "transformed_query_images": sum(image.get("preprocessing", {}).get("transformed", False)
                              for d in report["details"] for image in d.get("query_images", [])),
                          "include_dataset_captions": args.include_captions, "raw_images_used": args.with_images,
                          "image_attachment_role": "assistant (round-level adapter assumption)" if args.with_images else None,
                          "image_analysis_version": vision.version if vision else None,
                          "image_analysis_providers": vision.provenance if vision else [],
                          "return_policy": "actual media-budget compose, top_k counts messages",
                          "embedding_version": embedder.version if embedder else None,
                          "embedding_transport_policy": getattr(embedder, "transport_policy", None),
                          "chunk_characters": 400, "top_k_messages": args.top_k, "candidate_depth": report["candidate_depth"]}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / name
    atomic_json(output.with_suffix(".json"), report)
    output.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {output.with_suffix('.md').resolve()}")
    checkpoint.progress(status="completed", stage="completed", round_id=None, question_number=None,
                        report=str(output.with_suffix(".json").resolve()), error=None)


if __name__ == "__main__":
    cli_entrypoint(run)
