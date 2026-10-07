"""Seed a tiny story and print readable retrieved evidence."""
import argparse
import os
import httpx


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    headers = {"X-Api-Key": os.environ["MEMORY_API_KEY"]} if os.getenv("MEMORY_API_KEY") else {}
    with httpx.Client(base_url=args.url, headers=headers, timeout=180, trust_env=False) as client:
        response = client.post("/add", json={
            "request_id": "demo-story-v1", "user_id": "demo-local", "session_id": "demo-session",
            "messages": [{"role": "user", "content": text} for text in [
                "我最喜欢蓝色机械键盘。", "3号机床出现红色告警灯，需要停机检查。", "周末计划去杭州西湖旅行。"
            ]],
        })
        response.raise_for_status()
        for query in ["我喜欢什么键盘？", "哪台设备需要停机检查？", "周末要去哪儿旅行？"]:
            result = client.post("/search", json={"query": query, "user_id": "demo-local", "top_k": 1})
            result.raise_for_status()
            print("\n问题：" + query)
            for evidence in result.json()["data"]:
                print("找回证据：" + evidence["content"])


if __name__ == "__main__":
    run()
