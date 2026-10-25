"""Offline, real-loopback HTTP acceptance. Never reads .env or calls model APIs."""
import argparse
import base64
from datetime import datetime, timezone
import io
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
from PIL import Image
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
        try:
            process = start()
            result["checks"].append("clean-working-directory-startup")
            with httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                def check(ok):
                    if not ok:
                        raise RuntimeError("Deployment probe check failed")
                headers = {"X-Api-Key":api_key}
                user, request = "deployment-probe-user", "deployment-probe-request"
                content = [{"type":"text","text":"unique-image-evidence capacity-token before"}]
                for fmt, mime in (("PNG","png"),("JPEG","jpeg"),("WEBP","webp")):
                    stream = io.BytesIO()
                    Image.new("RGB", (8,8), "red").save(stream, fmt)
                    content.append({"type":"image_url","image_url":{"url":f"data:image/{mime};base64," + base64.b64encode(stream.getvalue()).decode()}})
                content.append({"type":"text","text":"after"})
                payload = {"request_id":request,"user_id":user,"session_id":"s","messages":[
                    {"role":"assistant","timestamp":1704067200000,"content":content},
                    *[{"role":"user","content":f"capacity-token item {i}"} for i in range(110)]]}
                phase = "authentication"
                check(client.post("/add", json=payload).status_code == 401)
                check(client.get("/health").status_code == 200)
                result["checks"].append(phase)
                phase = "synchronous-add-idempotency"
                response = client.post("/add", json=payload, headers=headers)
                check(response.status_code == 200)
                check(response.json() == {"success":True,"request_id":request,"user_id":user,"session_id":"s"})
                check(client.post("/add",json=payload,headers=headers).json() == response.json())
                result["checks"].append(phase)
                phase = "top-k-100-and-zero"
                query = {"query":"capacity-token","user_id":user,"top_k":100}
                data = client.post("/search", json=query, headers=headers).json()["data"]
                check(len(data)==100 and len({item["id"] for item in data})==100)
                ids = [item["id"] for item in data]
                check(client.post("/search",json={**query,"top_k":0},headers=headers).json()=={"data":[]})
                check(client.post("/search",json={**query,"top_k":101},headers=headers).status_code==422)
                result["checks"].append(phase)
                phase = "ordered-images-and-query-image"
                image_query = {"query":[content[1]],"user_id":user,"top_k":100}
                restored = client.post("/search",json=image_query,headers=headers).json()["data"]
                check(len(restored)==1 and restored[0]["content"]==content)
                image_id = restored[0]["id"]
                result["checks"].append(phase)
                phase = "bearer-token-and-isolation"
                for authorization in ("Bearer ", "Token "):
                    check(client.post("/search",json=image_query,headers={"Authorization":authorization+api_key}).status_code==200)
                check(client.post("/search",json={**query,"user_id":"other-user"},headers=headers).json()=={"data":[]})
                result["checks"].append(phase)
                phase = "conflict-body-limit-private-paths"
                conflict={**payload,"session_id":"changed"}
                check(client.post("/add",json=conflict,headers=headers).status_code==409)
                check(client.post("/add",content=b"x"*1048577,headers=headers).status_code==413)
                for path in ("/api/reports","/data","/.env"):
                    check(client.get(path).status_code==404)
                result["checks"].append(phase)
                phase = "restart-durability"
                stop(process)
                process = start()
                data = client.post("/search",json=query,headers=headers).json()["data"]
                check([item["id"] for item in data]==ids)
                restored = client.post("/search",json=image_query,headers=headers).json()["data"]
                check(restored[0]["id"]==image_id and restored[0]["content"]==content)
                result["checks"].append(phase)
            result["status"] = "passed"
        except Exception as exc:
            result.update(status="failed", phase=phase, error=error_feedback(exc))
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
