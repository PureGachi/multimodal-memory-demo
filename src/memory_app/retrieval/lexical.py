import math
import re
from collections import Counter
from memory_app.memory.models import Candidate


def tokens(text):
    result = re.findall(r"[a-z0-9_]+", text.lower())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(run)
        result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return result


def lexical_search(units, query, limit):
    query_terms = set(tokens(query))
    docs = [Counter(tokens(unit.text)) for unit in units]
    avg = sum(sum(doc.values()) for doc in docs) / max(len(docs), 1) or 1
    df = Counter(term for doc in docs for term in doc)
    results = []
    for unit, doc in zip(units, docs):
        size, score = sum(doc.values()), 0.0
        for term in query_terms:
            freq = doc[term]
            if freq:
                idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * size / avg))
        if score > 0:
            results.append(Candidate(unit.id, score))
    return sorted(results, key=lambda result: (-result.score, result.id))[:limit]
