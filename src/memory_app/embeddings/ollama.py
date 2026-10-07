import math
import httpx
from memory_app.memory.errors import ModelUnavailable


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Include model content digest, not just a mutable model tag.
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=10, trust_env=False)
            response.raise_for_status()
            entry = next(m for m in response.json()["models"] if m["name"] == model)
            self.version = f"ollama:{model}:{entry['digest']}:prefix-v1:chunk400"
        except (httpx.HTTPError, KeyError, ValueError, TypeError, StopIteration) as exc:
            raise ModelUnavailable("Ollama embedding model unavailable at startup") from exc

    def embed(self, texts, *, query=False):
        if not texts:
            return []
        prefix = "search_query: " if query else "search_document: "
        output = []
        try:
            for offset in range(0, len(texts), 16):
                batch = texts[offset:offset + 16]
                response = httpx.post(f"{self.base_url}/api/embed", json={
                    "model": self.model, "input": [prefix + text for text in batch],
                    "truncate": False,
                }, timeout=120, trust_env=False)
                response.raise_for_status()
                vectors = response.json()["embeddings"]
                if len(vectors) != len(batch):
                    raise ValueError("wrong vector count")
                for vector in vectors:
                    if not vector or not all(type(x) in (int, float) and math.isfinite(x) for x in vector):
                        raise ValueError("invalid vector")
                    norm = math.sqrt(sum(x * x for x in vector))
                    if norm == 0:
                        raise ValueError("zero vector")
                    output.append([x / norm for x in vector])
            if len({len(v) for v in output}) != 1:
                raise ValueError("inconsistent dimensions")
            return output
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise ModelUnavailable("Ollama embedding failed; no successful write was committed") from exc
