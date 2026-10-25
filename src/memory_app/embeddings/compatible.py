"""Model Studio's OpenAI-compatible text-embedding-v4 API (no paid retries)."""
import hashlib
import json
import math
import threading
from urllib.parse import urlsplit

import httpx

from memory_app.memory.errors import ModelUnavailable


class CompatibleEmbedder:
    def __init__(self, base_url, api_key, model="text-embedding-v4", dimensions=1024, revision="1", *, http_client=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or "{" in base_url or "}" in base_url):
            raise ValueError("EMBEDDING_BASE_URL requires a real HTTPS API base URL without credentials or query")
        if parsed.path.rstrip("/").endswith(("/embeddings", "/chat/completions")):
            raise ValueError("EMBEDDING_BASE_URL must be the API base URL, without /embeddings or /chat/completions")
        if not api_key.strip() or model != "text-embedding-v4":
            raise ValueError("Embedding API requires EMBEDDING_API_KEY and text-embedding-v4")
        if type(dimensions) is not int or dimensions not in {64, 128, 256, 512, 768, 1024, 1536, 2048}:
            raise ValueError("Invalid text-embedding-v4 dimensions")
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.dimensions = dimensions
        identity = json.dumps([self.base_url, model, dimensions, revision, "raw-input:l2:chunk400:v1"])
        self.version = f"compatible:{model}:" + hashlib.sha256(identity.encode()).hexdigest()
        self._client, self._closed = http_client, False
        self._client_lock = threading.Lock()
        self.transport_policy = "httpx-pooled:trust_env=false:default-TLS-verification:no-retries"

    def _post(self, url, *, trust_env=False, **kwargs):
        if trust_env:
            raise ValueError("Embedding client does not inherit environment proxies")
        with self._client_lock:
            if self._closed:
                raise ModelUnavailable("Embedding client is closed", component="embedding")
            if self._client is None:
                self._client = httpx.Client(trust_env=False, timeout=120,
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=2, keepalive_expiry=30))
            client = self._client
        return client.post(url, **kwargs)

    def close(self):
        with self._client_lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
        if client is not None:
            client.close()

    def embed(self, texts, *, query=False):
        # The compatible endpoint has no text_type parameter. Use raw text for
        # both roles, never Ollama/nomic's search_query/search_document prefixes.
        if not texts:
            return []
        output = []
        try:
            for offset in range(0, len(texts), 10):
                batch = texts[offset:offset + 10]
                response = self._post(self.base_url + "/embeddings", headers={
                    "Authorization": f"Bearer {self.api_key}",
                }, json={"model": self.model, "input": batch,
                         "dimensions": self.dimensions, "encoding_format": "float"},
                    timeout=120, trust_env=False)
                response.raise_for_status()
                rows = response.json()["data"]
                if not isinstance(rows, list) or len(rows) != len(batch):
                    raise ValueError("Wrong vector count")
                indices = [row["index"] for row in rows]
                if any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(batch))):
                    raise ValueError("Invalid vector indices")
                for row in sorted(rows, key=lambda item: item["index"]):
                    vector = row["embedding"]
                    if (not isinstance(vector, list) or len(vector) != self.dimensions
                            or not all(type(x) in (int, float) and math.isfinite(x) for x in vector)):
                        raise ValueError("Invalid vector")
                    norm = math.hypot(*vector)
                    if not math.isfinite(norm) or norm == 0:
                        raise ValueError("Invalid vector norm")
                    output.append([x / norm for x in vector])
            return output
        except httpx.HTTPStatusError as exc:
            # Never expose provider messages, unknown codes, or chained errors.
            code = "unclassified"
            try:
                value = exc.response.json().get("error", {}).get("code")
                if isinstance(value, str) and value in {"invalid_api_key", "insufficient_quota",
                        "rate_limit_exceeded", "model_not_found", "InvalidApiKey", "Throttling",
                        "Arrearage", "AccessDenied", "InvalidParameter", "invalid_parameter_error"}:
                    code = value
            except (ValueError, TypeError, AttributeError):
                pass
            raise ModelUnavailable(f"Embedding API failed (HTTP {exc.response.status_code}, code={code}); check embedding endpoint, model access, key region or quota",
                code="http_error", component="embedding", http_status=exc.response.status_code, provider_code=code,
                retryable=exc.response.status_code >= 500 or (exc.response.status_code == 429 and code not in {"insufficient_quota", "Arrearage"})) from None
        except httpx.TimeoutException as exc:
            from memory_app.diagnostics import classify_failure
            raise ModelUnavailable("Embedding API timed out", code="timeout", component="embedding", retryable=True,
                                   diagnostic=classify_failure(exc)) from None
        except httpx.HTTPError as exc:
            from memory_app.diagnostics import classify_failure
            raise ModelUnavailable("Embedding API network failure", code="network_error", component="embedding", retryable=True,
                                   diagnostic=classify_failure(exc)) from None
        except (KeyError, IndexError, ValueError, TypeError, OverflowError):
            raise ModelUnavailable("Embedding API returned invalid vectors", code="invalid_response", component="embedding") from None
