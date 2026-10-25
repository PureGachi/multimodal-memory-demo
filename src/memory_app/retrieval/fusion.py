from memory_app.memory.models import Candidate
import math


def rrf(rank_lists, k=60, *, weights=None):
    """Weighted rank fusion; defaults preserve the original equal-weight policy."""
    if type(k) not in (int, float) or not math.isfinite(k) or k < 0:
        raise ValueError("RRF k must be finite and nonnegative")
    rank_lists = list(rank_lists)
    weights = [1.0] * len(rank_lists) if weights is None else list(weights)
    if (len(weights) != len(rank_lists) or any(type(w) not in (int, float)
            or not math.isfinite(w) or w < 0 for w in weights)
            or (weights and not any(weights))):
        raise ValueError("RRF weights must match channels, be finite/nonnegative and not all zero")
    scores = {}
    for candidates, weight in zip(rank_lists, weights):
        if weight == 0:
            continue
        seen = set()
        for rank, candidate in enumerate(candidates, 1):
            if candidate.id not in seen:
                scores[candidate.id] = scores.get(candidate.id, 0) + weight / (k + rank)
                seen.add(candidate.id)
    return [Candidate(id, score) for id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
