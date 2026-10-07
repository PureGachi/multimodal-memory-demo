from memory_app.memory.models import Candidate


def rrf(rank_lists, k=60):
    scores = {}
    for candidates in rank_lists:
        seen = set()
        for rank, candidate in enumerate(candidates, 1):
            if candidate.id not in seen:
                scores[candidate.id] = scores.get(candidate.id, 0) + 1 / (k + rank)
                seen.add(candidate.id)
    return [Candidate(id, score) for id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
