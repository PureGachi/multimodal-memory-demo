"""Download only images referenced by one local Mem-Gallery scene; no token."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
import httpx
from memory_app.evaluation.gallery_media import image_reference, validate_original, MAX_ORIGINAL
from memory_app.media.validation import MAX_IMAGE
from memory_app.errors import cli_entrypoint


def retry_download(action, attempts=3):
    """Only retries public dataset reads, never model requests."""
    for attempt in range(attempts):
        try:
            return action()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise
            if attempt + 1 == attempts:
                raise
            print(f"Dataset connection interrupted; retry {attempt + 1}/{attempts - 1}", flush=True)
            time.sleep(min(2 ** attempt, 4))


def download_file(client, url, target, *, expected=None, allow_large=False):
    limit = MAX_ORIGINAL if allow_large else MAX_IMAGE
    def validate(raw):
        if len(raw) > limit:
            raise ValueError("Oversized original requires --allow-large-originals (download only)")
        if expected and hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Dataset checksum mismatch; refusing to overwrite originals")
        validate_original(raw)
    if target.is_file():
        if target.stat().st_size > limit:
            raise ValueError("Oversized original requires --allow-large-originals")
        raw = target.read_bytes()
        validate(raw)
        return len(raw), "existing"
    def fetch():
        with client.stream("GET", url) as response:
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and length.isdecimal() and int(length) > limit:
                raise ValueError("Oversized original requires --allow-large-originals (maximum 64 MiB)")
            chunks, count = [], 0
            for chunk in response.iter_bytes():
                count += len(chunk)
                if count > limit:
                    raise ValueError("Dataset image exceeds configured download limit")
                chunks.append(chunk)
        result = b"".join(chunks)
        validate(result)
        return result
    raw = retry_download(fetch)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Publish only complete, verified bytes. Hard-link creation never overwrites.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".download-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            validate(target.read_bytes())
            if target.read_bytes() != raw:
                raise ValueError("Concurrent download has different bytes; refusing overwrite")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(raw), "downloaded"


def referenced_images(data, dialog_path):
    """Both historical evidence and pictures supplied with evaluation questions."""
    files = {}
    for session in data["multi_session_dialogues"]:
        for turn in session["dialogues"]:
            for reference in turn.get("input_image", []):
                target, relative = image_reference(dialog_path, reference)
                files[relative] = target
    for qa in data["human-annotated QAs"]:
        reference = qa.get("question_image")
        if reference:
            target, relative = image_reference(dialog_path, reference)
            files[relative] = target
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/benchmarks/mem-gallery/data/dialog/AI_Robotics_Automation_Future_Tech.json"))
    parser.add_argument("--endpoint", choices=["https://hf-mirror.com", "https://huggingface.co"], default="https://hf-mirror.com")
    parser.add_argument("--allow-large-originals", action="store_true", help="Preserve originals up to 64 MiB; API still limits images to 10 MiB")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    data = json.loads(args.data.read_text(encoding="utf-8-sig"))
    files = referenced_images(data, args.data)
    if not files:
        raise SystemExit("No referenced images")
    client = httpx.Client(timeout=60, follow_redirects=True, trust_env=False)
    # Get per-file LFS SHA-256 when available, rather than trusting a filename.
    hashes = {}
    for directory in sorted({str(Path(name).parent).replace("\\", "/") for name in files}):
        def fetch_index():
            response = client.get(args.endpoint + "/api/datasets/Ethan-Bei/Mem-Gallery/tree/main/data/" + quote(directory, safe="/"))
            response.raise_for_status()
            return response
        response = retry_download(fetch_index)
        for item in response.json():
            if item.get("type") == "file":
                hashes[item["path"]] = item.get("lfs", {}).get("oid")
    def download(item):
        relative, target = item
        expected = hashes.get("data/" + relative)
        url = args.endpoint + "/datasets/Ethan-Bei/Mem-Gallery/resolve/main/data/" + quote(relative, safe="/")
        try:
            size, state = download_file(client, url, target, expected=expected, allow_large=args.allow_large_originals)
            return relative, size, state + (" (oversized original; evaluation needs --compress-oversize)" if size > MAX_IMAGE else "")
        except Exception:
            print(f"Failed dataset file: {relative}", flush=True)
            raise
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for i, (name, size, state) in enumerate(pool.map(download, files.items()), 1):
                print(f"{i}/{len(files)} {state} {name} ({size} bytes)", flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    cli_entrypoint(main)
