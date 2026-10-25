"""Bound resource use before JSON/Base64 parsing; no payloads are logged."""
import asyncio
import secrets
import threading

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


def valid_credentials(api_key, provided, authorization):
    if not api_key:
        return True
    if authorization.startswith(("Bearer ", "Token ")):
        provided = authorization.split(" ", 1)[1]
    return secrets.compare_digest(provided.encode(), api_key.encode())


class ServiceLimits:
    def __init__(self, app, *, api_key, max_bytes, max_inflight, body_timeout):
        self.app, self.api_key = app, api_key
        self.max_bytes, self.max_inflight, self.body_timeout = max_bytes, max_inflight, body_timeout
        self.inflight, self.guard = 0, threading.Lock()

    async def reject(self, scope, receive, send, status, code, message, *, retryable=False):
        response = JSONResponse(status_code=status,
            content={"detail": message, "error": {"code": code, "component": "api",
                "retryable": retryable, "http_status": status, "provider_code": None,
                "message": message, "hint": "服务不会截断输入或自动重试模型请求。"}},
            headers={"Retry-After": "1"} if status == 503 else None)
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") not in {"/add", "/search"}:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        if not valid_credentials(self.api_key, headers.get("x-api-key", ""), headers.get("authorization", "")):
            return await self.reject(scope, receive, send, 401, "unauthorized", "Invalid API key")
        length = headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                return await self.reject(scope, receive, send, 400, "invalid_length", "Invalid Content-Length")
            # Avoid parsing unbounded decimal strings into integers.
            significant = length.lstrip("0") or "0"
            if len(significant) > 16 or int(significant) > self.max_bytes:
                return await self.reject(scope, receive, send, 413, "request_too_large", "请求体超过当前服务配置的字节上限")
        with self.guard:
            accepted = self.inflight < self.max_inflight
            if accepted:
                self.inflight += 1
        if not accepted:
            return await self.reject(scope, receive, send, 503, "service_busy", "服务并发容量已满", retryable=True)
        try:
            chunks, size = [], 0
            try:
                async with asyncio.timeout(self.body_timeout):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > self.max_bytes:
                            return await self.reject(scope, receive, send, 413, "request_too_large", "请求体超过当前服务配置的字节上限")
                        chunks.append(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await self.reject(scope, receive, send, 408, "body_timeout", "接收请求体超时", retryable=True)
            body, delivered = b"".join(chunks), False
            async def replay():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await self.app(scope, replay, send)
        finally:
            with self.guard:
                self.inflight -= 1
