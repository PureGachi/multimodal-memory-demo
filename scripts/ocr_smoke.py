"""Real local OCR + lexical retrieval on three generated, ideal text images."""
import argparse
import base64
import json
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from memory_app.config import Settings
from memory_app.main import create_app


CASES = [
    ("equipment", "设备型号：M42\n设备名称：冷却水泵\n维护提示：每周检查水位", "冷却水泵"),
    ("receipt", "采购记录\n商品：咖啡豆\n批次：K88\n金额：128元", "K88"),
    ("travel", "出行记录\n目的地：杭州\n车次：T17\n出发时间：08:30", "T17"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    candidate = Path(sys.prefix) / "Library/bin/tesseract.exe"
    parser.add_argument("--tesseract", default=str(candidate) if candidate.exists() else "tesseract")
    parser.add_argument("--languages", default="chi_sim")
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/msyh.ttc"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/ocr"))
    args = parser.parse_args()
    font = ImageFont.truetype(str(args.font), 44)
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = args.output_dir / identity
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings(embeddings="none", vision="none", ocr="tesseract",
                        tesseract_executable=args.tesseract, ocr_languages=args.languages)
    samples = []
    with tempfile.TemporaryDirectory(prefix="memory-real-ocr-") as folder:
        from dataclasses import replace
        service = create_app(replace(settings, data_dir=Path(folder))).state.service
        for name, text, query in CASES:
            image = Image.new("RGB", (1100, 420), "white")
            ImageDraw.Draw(image).multiline_text((50, 35), text, font=font, fill="black", spacing=25)
            target = output / (name + ".png")
            image.save(target)
            uri = "data:image/png;base64," + base64.b64encode(target.read_bytes()).decode()
            service.add({"request_id": name, "user_id": "ocr-smoke", "session_id": "ocr-smoke",
                         "messages": [{"role": "user", "content": [
                             {"type": "image_url", "image_url": {"url": uri}}]}]})
            samples.append({"name": name, "image": target.name, "printed_text": text, "query": query})
        units = service.dependencies[0].list_units("ocr-smoke")
        for sample in samples:
            # Unit identity is deterministic for the request and message order.
            expected = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["ocr-smoke", sample["name"], 0], ensure_ascii=False)))
            unit = next(u for u in units if u.id == expected)
            result = service.search({"user_id": "ocr-smoke", "query": sample["query"], "top_k": 3})
            sample["unit_id"] = expected
            sample["visible_text"] = unit.image_artifacts[0]["visible_text"]
            sample["query_present_ignoring_whitespace"] = "".join(sample["query"].split()) in "".join(sample["visible_text"].split())
            sample["returned_ids"] = [item["id"] for item in result["data"]]
            by_id = {item.id: item for item in units}
            sample["returned"] = []
            for rank, item in enumerate(result["data"], 1):
                source = next(s for s in samples if str(uuid.uuid5(uuid.NAMESPACE_URL,
                              json.dumps(["ocr-smoke", s["name"], 0], ensure_ascii=False))) == item["id"])
                sample["returned"].append({"rank": rank, "memory_id": item["id"], "score": item["score"],
                    "role": by_id[item["id"]].role, "content": "", "images": [source["image"]],
                    "image_artifacts": by_id[item["id"]].image_artifacts})
            sample["top1_correct"] = bool(sample["returned_ids"] and sample["returned_ids"][0] == expected)
            expected_uri = "data:image/png;base64," + base64.b64encode((output / sample["image"]).read_bytes()).decode()
            sample["original_image_restored"] = bool(result["data"] and any(
                part["type"] == "image_url" and part["image_url"]["url"] == expected_uri
                for part in result["data"][0]["content"]))
        negative = service.search({"user_id": "ocr-smoke", "query": "ZXQ999_NOT_FOUND", "top_k": 3})
        report = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "pipeline_version": service.vision.version, "providers": service.vision.provenance,
                  "embeddings": "none", "vision_api": "none", "samples": samples,
                  "report_type": "ocr", "top_k": 3,
                  "negative_query": "ZXQ999_NOT_FOUND", "negative_returned_ids": [r["id"] for r in negative["data"]],
                  "note": "Generated clean text images, real OCR and BM25. Not a representative accuracy benchmark."}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 本地 OCR 实测", "", report["note"], "",
             "测试图为程序生成的清晰大字图；没有付费调用，没有使用人工预写描述入库。", ""]
    for sample in samples:
        lines.extend([f"## {sample['name']}", "", f"![测试图片]({sample['image']})", "",
                      "实际 OCR：", "", "```text", sample["visible_text"], "```", "",
                      f"查询：{sample['query']}；目标文字被识别：{sample['query_present_ignoring_whitespace']}；首条正确：{sample['top1_correct']}；原图字节一致：{sample['original_image_restored']}", ""])
    lines.extend(["## 无证据检查", "", f"查询 ZXQ999_NOT_FOUND，返回 {len(negative['data'])} 条。仅代表此例 BM25 行为，不是通用拒答能力。", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    for sample in samples:
        print(f"{sample['name']}: query={sample['query']!r}, top1={sample['top1_correct']}, OCR={sample['visible_text']!r}")
    print(f"Report: {output / 'report.md'}")
    if negative["data"] or not all(s["top1_correct"] and s["original_image_restored"]
                                   and s["query_present_ignoring_whitespace"] for s in samples):
        raise SystemExit("OCR smoke has failed cases; inspect report before changing the model or retrieval.")


if __name__ == "__main__":
    main()
