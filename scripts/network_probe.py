"""No API key, no image, no inference: inspect the configured vision route."""
import json
import os
from urllib.parse import urlsplit

import httpx
from memory_app.config import Settings
from memory_app.diagnostics import classify_failure, HINTS
from memory_app.errors import cli_entrypoint


def probe(base_url, *, trust_env=False):
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Expected HTTPS endpoint without embedded credentials")
    try:
        # Deliberately no Authorization header or image. Never follow redirects.
        with httpx.Client(timeout=10, trust_env=trust_env, follow_redirects=False) as client:
            response = client.post(base_url.rstrip("/") + "/chat/completions", json={})
        return {"trust_env": trust_env, "http_status": response.status_code,
                "meaning": "收到 HTTP 响应；不表示模型、鉴权或图片请求已通过。"}
    except httpx.HTTPError as exc:
        reason = classify_failure(exc)
        return {"trust_env": trust_env, "diagnostic": reason, "hint": HINTS[reason]}


def main():
    settings = Settings.from_env()
    print("仅检测连接：不发送 API Key、图片或模型请求；不会更改代理设置。", flush=True)
    print(json.dumps({"proxy_environment_present": any(os.environ.get(k) for k in
        ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"))}, ensure_ascii=False))
    for mode in (False, True):
        print(json.dumps(probe(settings.vision_base_url, trust_env=mode), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    cli_entrypoint(main)
