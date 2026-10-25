"""External HTTP smoke with unique users; no API key printed."""
import argparse
import base64
import io
import json
import os
import uuid
import httpx
from PIL import Image
from dotenv import dotenv_values
from memory_app.errors import cli_entrypoint


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", default=".env", help="Read only the service API key; environment takes priority")
    parser.add_argument("--allow-model-calls", action="store_true", help="Allow writes/searches that may invoke paid models on the target service")
    args = parser.parse_args()
    scope = "smoke-" + uuid.uuid4().hex
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    content = [{"type": "text", "text": "3号机床出现红色告警灯"},
               {"type": "image_url", "image_url": {"url": uri}}]
    values = dotenv_values(args.env_file,encoding="utf-8-sig",interpolate=False)
    key = os.environ.get("MEMORY_API_KEY",values.get("MEMORY_API_KEY") or "")
    headers = {"X-Api-Key": key} if key else {}
    with httpx.Client(base_url=args.url, headers=headers, timeout=180, trust_env=False) as client:
        health=client.get("/health")
        health.raise_for_status()
        configuration=health.json()
        if not args.allow_model_calls and (configuration.get("embeddings")!="none" or configuration.get("image_analysis") is not False):
            parser.error("目标服务可能调用模型；确认费用后加 --allow-model-calls，或先运行零费用 deployment_smoke.py")
        body = {"request_id": scope + ":r1", "user_id": scope, "session_id": scope + ":s1",
                "messages": [{"role": "user", "content": content}]}
        for _ in range(2):
            added = client.post("/add", json=body)
            added.raise_for_status()
            assert added.json() == {key: body[key] for key in ("request_id", "user_id", "session_id")} | {"success": True}
        query = {"query": "哪台机床出现红色告警灯？", "user_id": scope, "top_k": 3}
        found = client.post("/search", json=query)
        found.raise_for_status()
        results = found.json()["data"]
        assert len(results) == 1 and results[0]["content"] == content
        query["user_id"] += "-other"
        isolated = client.post("/search", json=query)
        isolated.raise_for_status()
        assert isolated.json() == {"data": []}
        print(json.dumps({"status": "passed", "user_id": scope, "results": len(results),
                          "checks": ["health", "add", "retry", "search", "image-roundtrip", "isolation"]}, ensure_ascii=False))


if __name__ == "__main__":
    cli_entrypoint(run)
