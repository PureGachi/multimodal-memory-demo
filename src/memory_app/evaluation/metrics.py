"""Pure retrieval metrics: no settings, model clients or filesystem access."""


def rank_metrics(round_ids, clues):
    """K is returned messages; repeated rounds count only once towards recall."""
    gold = set(clues)
    if not gold:
        return None
    found = gold.intersection(round_ids)
    first = next((i for i, value in enumerate(round_ids, 1) if value in gold), None)
    return {"hit_at_k": int(bool(found)), "recall_at_k": len(found) / len(gold),
            "mrr_at_k": 1 / first if first else 0.0,
            "first_evidence_rank": first, "all_evidence_found": int(found == gold)}


def summarize(details, names):
    result = {}
    for name in names:
        scored = [d["channels"][name]["metrics"] for d in details
                  if d["channels"][name]["metrics"] is not None]
        result[name] = {"scored_questions": len(scored)}
        for field in ("hit_at_k", "recall_at_k", "mrr_at_k", "all_evidence_found"):
            result[name][field] = sum(m[field] for m in scored) / len(scored) if scored else None
    return result


def summarize_images(details, names):
    result = {}
    for name in names:
        scored = [detail["channels"][name]["image_metrics"] for detail in details
                  if detail["channels"][name]["image_metrics"] is not None]
        result[name] = {"scored_questions": len(scored)}
        for field in ("hit_at_k", "recall_at_k", "all_evidence_found"):
            result[name][field] = sum(item[field] for item in scored) / len(scored) if scored else None
    return result
