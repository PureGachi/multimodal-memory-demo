import pytest
from memory_app.evaluation.embedding_probe import run_probe
from memory_app.memory.errors import ModelUnavailable


class Embedder:
    version = "probe-controlled"

    def __init__(self):
        self.calls = []

    def embed(self, texts, *, query=False):
        self.calls.append((texts, query))
        if query:
            return [[1., 0., 0.] for _ in texts]
        return [[1., 0., 0.] if "M42" in text else [0., 1., 0.] if "键盘" in text else [0., 0., 1.] for text in texts]


def test_probe_actual_add_search_and_isolation():
    embedder = Embedder()
    result = run_probe(embedder)
    assert len(embedder.calls) == 2
    assert len(embedder.calls[0][0]) == 3 and not embedder.calls[0][1]
    assert embedder.calls[1][1]
    assert result["observed_dimensions"] == [3]
    assert result["dense_top1_expected"] and result["fusion_top1_expected"]
    assert result["isolated_user_result_count"] == 0
    assert len(result["dense_results"]) == 3


def test_probe_does_not_count_lexical_fallback_as_embedding_success():
    class FailingQuery(Embedder):
        def embed(self, texts, *, query=False):
            if query:
                raise ModelUnavailable("controlled failure")
            return super().embed(texts, query=query)
    with pytest.raises(ModelUnavailable, match="lexical fallback is not success"):
        run_probe(FailingQuery())
