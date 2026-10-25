"""Small actual Add/Search probe, independent of image analysis and real data."""
from pathlib import Path
import tempfile

from memory_app.media.local import LocalMediaStore
from memory_app.memory.service import MemoryService
from memory_app.storage.sqlite import SQLiteStore
from memory_app.vector_stores.sqlite import SQLiteVectorStore
from memory_app.memory.errors import ModelUnavailable


DOCUMENTS = ["设备型号：M42。设备名称：冷却水泵。维护提示：每周检查水位。",
             "我喜欢蓝色机械键盘，使用时希望按键声音小一些。",
             "本周末计划去杭州西湖旅行，已经订好车票。"]
QUERY = "负责给设备降温的水泵，应当多久查看一次水位？"


def run_probe(embedder):
    class ObservedEmbedder:
        version = embedder.version

        def __init__(self):
            self.calls = 0
            self.query_vectors = None
            self.document_vectors = []

        def embed(self, texts, *, query=False):
            self.calls += 1
            result = embedder.embed(texts, query=query)
            if query:
                self.query_vectors = result
            else:
                self.document_vectors.extend(result)
            return result

    observed = ObservedEmbedder()
    with tempfile.TemporaryDirectory(prefix="memory-embedding-probe-") as folder:
        root = Path(folder)
        store = SQLiteStore(root / "memory.sqlite3")
        vectors = SQLiteVectorStore(store)
        service = MemoryService(store, LocalMediaStore(root / "media"), observed, vectors)
        service.add({"user_id": "embedding-probe", "session_id": "probe", "request_id": "probe",
                     "messages": [{"role": "user", "content": text} for text in DOCUMENTS]})
        result = service.search({"user_id": "embedding-probe", "query": QUERY, "top_k": 3})
        # Search normally falls back to BM25 on vector failures. A probe must
        # never report that fallback as a successful embedding connection.
        if not observed.query_vectors:
            raise ModelUnavailable("Embedding probe failed: query embedding unavailable (lexical fallback is not success)")
        units = {unit.id: unit for unit in store.list_units("embedding-probe")}
        dense = vectors.search("embedding-probe", embedder.version, observed.query_vectors, 3)
        expected_id = next(unit.id for unit in units.values() if unit.text == DOCUMENTS[0])
        isolated = vectors.search("different-user", embedder.version, observed.query_vectors, 3)
        dense_rows = [{"rank": rank, "id": item.id, "score": item.score,
                       "content": units[item.id].content} for rank, item in enumerate(dense, 1)]
        sizes = sorted({len(vector) for vector in observed.document_vectors + observed.query_vectors})
        return {"query": QUERY, "documents": DOCUMENTS, "expected_id": expected_id,
                "embedding_version": embedder.version, "observed_dimensions": sizes,
                "embedding_calls": observed.calls, "dense_results": dense_rows,
                "fusion_results": result["data"], "isolated_user_result_count": len(isolated),
                "dense_top1_expected": bool(dense and dense[0].id == expected_id),
                "fusion_top1_expected": bool(result["data"] and result["data"][0]["id"] == expected_id),
                "note": "Synthetic 3-document plumbing/semantic smoke check, not retrieval accuracy or official evaluation; no images or LLM calls."}
