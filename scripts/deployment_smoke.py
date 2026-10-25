"""Offline, real-loopback HTTP acceptance. Never reads .env or calls model APIs."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
from _http_checks import exercise_http
from memory_app.errors import error_feedback
from memory_app.evaluation.checkpoint import atomic_json


def run_probe(*, python=sys.executable, installed=False):
    result = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
              "kind": "offline-deployment-http", "remote_model_calls": 0, "checks": [],
              "package_mode": "installed-wheel" if installed else "workspace-source",
              "scope": "controlled no-model fixtures; not official Smoke or remote model quality",
              "request_limit_tested_bytes": 1048576}
    process, phase = None, "prepare"
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="memory-deployment-probe-") as folder:
        root = Path(folder)
        env = {k:v for k,v in os.environ.items() if not k.startswith(
            ("MEMORY_", "VISION_", "EMBEDDING_", "OLLAMA_", "OCR_", "TESSERACT_"))}
        env.pop("PYTHONPATH", None)
        if not installed:
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        api_key = uuid.uuid4().hex
        env.update(MEMORY_PROFILE="local", MEMORY_EMBEDDINGS="none", MEMORY_VISION="none", MEMORY_OCR="none",
                   MEMORY_API_KEY=api_key, MEMORY_DATA_DIR=str(root / "data"), MEMORY_MAX_REQUEST_BYTES="1048576",
                   MEMORY_MAX_INFLIGHT="2", MEMORY_BODY_TIMEOUT_SECONDS="30", PYTHONNOUSERSITE="1", PYTHONUTF8="1")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        def start():
            child = subprocess.Popen([str(python), "-m", "uvicorn", "memory_app.main:create_app", "--factory",
                "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--no-access-log"],
                cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + 25
                with httpx.Client(timeout=1, trust_env=False) as client:
                    while time.monotonic() < deadline:
                        if child.poll() is not None:
                            raise RuntimeError("Probe server exited during startup")
                        try:
                            if client.get(url + "/health").status_code == 200:
                                return child
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                raise RuntimeError("Probe server startup timed out")
            except BaseException:
                stop(child)
                raise
        def stop(child):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        def restart():
            nonlocal process
            stop(process)
            process = start()
        try:
            process = start()
            result["checks"].append("clean-working-directory-startup")
            with httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                exercise_http(client, api_key, restart, result["checks"])
            result["status"] = "passed"
        except Exception as exc:
            result.update(status="failed", phase=getattr(exc, "phase", phase), error=error_feedback(exc))
        finally:
            stop(process)
    result["elapsed_seconds"] = round(time.monotonic()-started,3)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Interpreter used by the isolated child server")
    parser.add_argument("--installed", action="store_true", help="Use installed package instead of source PYTHONPATH")
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/deployment"))
    args=parser.parse_args()
    report=run_probe(python=args.python, installed=args.installed)
    output=args.output_dir/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]+".json")
    atomic_json(output, report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print("Report:",output.resolve())
    if report["status"]!="passed":raise SystemExit(1)


if __name__=="__main__":
    main()
