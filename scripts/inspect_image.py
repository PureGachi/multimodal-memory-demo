"""Analyze one local image, search it by text, and save an inspection report."""
import argparse
import base64
import json
import tempfile
import uuid
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from memory_app.config import Settings
from memory_app.main import create_app
from memory_app.media.validation import decode_image
from memory_app.errors import cli_entrypoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/images"))
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.vision == "none" and settings.ocr == "none":
        parser.error("Enable MEMORY_VISION or MEMORY_OCR first")
    if settings.vision != "none" and not args.allow_remote:
        parser.error("Remote vision requires --allow-remote; image upload may incur fees")
    suffix = args.image.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix)
    if mime is None or args.image.stat().st_size > 10 * 1024 * 1024:
        parser.error("Choose a PNG/JPEG/WebP image no larger than 10 MiB")
    uri = f"data:{mime};base64,{base64.b64encode(args.image.read_bytes()).decode()}"
    decode_image(uri)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    copied_image = name + suffix
    shutil.copyfile(args.image, args.output_dir / copied_image)
    with tempfile.TemporaryDirectory(prefix="memory-image-inspect-") as folder:
        service = create_app(replace(settings, data_dir=Path(folder))).state.service
        service.add({"user_id": "image-inspect", "request_id": "one", "session_id": "one",
                     "messages": [{"role": "user", "content": [
                         {"type": "image_url", "image_url": {"url": uri}}]}]})
        unit = service.dependencies[0].list_units("image-inspect")[0]
        result = service.search({"user_id": "image-inspect", "query": args.query, "top_k": 3})
        report = {"report_type": "image-inspection", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "image": copied_image, "query": args.query,
                  "image_artifacts": unit.image_artifacts,
                  "retrieved": [{**{k: v for k, v in row.items() if k != "content"},
                                 "content": "", "images": [copied_image],
                                 "image_artifacts": unit.image_artifacts} for row in result["data"]],
                  "note": "Single-image plumbing check, not retrieval accuracy. Inspect extracted text manually."}
    target = args.output_dir / (name + ".json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {target}; retrieved messages: {len(report['retrieved'])}")


if __name__ == "__main__":
    cli_entrypoint(main)
