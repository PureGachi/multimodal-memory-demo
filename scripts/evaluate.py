"""Tiny diagnostic set: lexical vs dense vs fusion; not official scoring."""
import io
import json
import base64
import tempfile
from pathlib import Path
from PIL import Image
from memory_app.config import Settings
from memory_app.embeddings.ollama import OllamaEmbedder
from memory_app.media.local import LocalMediaStore
from memory_app.storage.sqlite import SQLiteStore
from memory_app.vector_stores.sqlite import SQLiteVectorStore
from memory_app.memory.add import add_memory
from memory_app.retrieval.lexical import lexical_search
from memory_app.retrieval.fusion import rrf


def run():
    settings = Settings.from_env()
    embedder = OllamaEmbedder(settings.ollama_url, settings.embed_model)
    texts = ["我最喜欢蓝色机械键盘。", "3号机床出现红色告警灯，需要停机检查。",
             "周末计划去杭州西湖旅行。", "联系我时优先发邮件，不要打电话。",
             "去年我住在上海。", "这个月我搬到了苏州。"]
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, "PNG")
    image = [{"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()}}]
    cases = [("我喜欢什么键盘？", [0], "exact"),
             ("哪台设备需要停机检查？", [1], "exact"),
             ("周末要去哪儿旅行？", [2], "exact"),
             ("我的输入设备偏好是什么？", [0], "paraphrase"),
             ("如何联系我比较合适？", [3], "paraphrase"),
             ("我以前和现在分别住在哪儿？", [4, 5], "multi-evidence"),
             ("有哪些城市与我的旅行和居住有关？", [2, 4, 5], "multi-evidence"),
             ("找出那张只有红色的图片", [6], "visual-gap")]
    with tempfile.TemporaryDirectory(prefix="memory-eval-") as folder:
        root = Path(folder)
        store, media = SQLiteStore(root / "memory.sqlite3"), LocalMediaStore(root / "media")
        vectors = SQLiteVectorStore(store)
        add_memory({"request_id": "eval", "user_id": "eval", "session_id": "eval", "messages":
                    [{"role": "user", "content": text} for text in [*texts, image]]},
                   store, media, embedder, vectors)
        units = store.list_units("eval")
        by_text = {unit.content: unit.id for unit in units if isinstance(unit.content, str)}
        ids = [by_text[text] for text in texts] + [next(u.id for u in units if isinstance(u.content, list))]
        details, totals = [], {name: {"hit_at_1": 0, "recall_at_3": 0.0} for name in ["lexical", "dense", "fusion"]}
        queries = embedder.embed([question for question, _, _ in cases] + ["我的护照号码是多少？"], query=True)
        for (question, expected, kind), qvec in zip(cases, queries):
            gold = {ids[index] for index in expected}
            lexical = lexical_search(units, question, 30)
            dense = vectors.search("eval", embedder.version, [qvec], 30)
            channels = {"lexical": lexical, "dense": dense, "fusion": rrf([lexical, dense])}
            scores = {}
            for name, candidates in channels.items():
                returned = [c.id for c in candidates[:3]]
                hit = int(bool(returned) and returned[0] in gold)
                recall = len(set(returned) & gold) / len(gold)
                totals[name]["hit_at_1"] += hit
                totals[name]["recall_at_3"] += recall
                scores[name] = {"hit_at_1": hit, "recall_at_3": round(recall, 3)}
            details.append({"query": question, "kind": kind, "scores": scores})
        summary = {name: {"hit_at_1": round(value["hit_at_1"] / len(cases), 3),
                          "recall_at_3": round(value["recall_at_3"] / len(cases), 3)}
                   for name, value in totals.items()}
        unknown = "我的护照号码是多少？"
        lex = lexical_search(units, unknown, 30)
        dense = vectors.search("eval", embedder.version, [queries[-1]], 30)
        print(json.dumps({"cases": len(cases), "summary": summary, "details": details,
                          "no_evidence_probe": {"query": unknown, "lexical_count": len(lex),
                                                "dense_count": len(dense), "fusion_count": len(rrf([lex, dense]))}},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
