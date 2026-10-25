"""Check remote embedding configuration, or run a tiny paid Add/Search probe."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from memory_app.config import Settings
from memory_app.embeddings.factory import build_embedder
from memory_app.evaluation.embedding_probe import run_probe
from memory_app.errors import cli_entrypoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="No network or writes; never prints keys")
    parser.add_argument("--allow-remote", action="store_true", help="Consent to 2 embedding requests (3 documents + 1 query), no image/LLM requests")
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/embeddings"))
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.check_only:
        print(json.dumps({"mode": settings.embeddings, "model": settings.embedding_model,
                          "dimensions": settings.embedding_dimensions,
                          "endpoint_configured": bool(settings.embedding_base_url.strip()),
                          "key_configured": bool(settings.embedding_api_key.strip()),
                          "network_called": False}, ensure_ascii=False, indent=2))
        return
    if settings.embeddings != "compatible":
        parser.error("Set MEMORY_EMBEDDINGS=compatible in .env for text-embedding-v4")
    if not args.allow_remote:
        parser.error("Use --allow-remote to consent to paid embedding requests; start with --check-only")
    report = run_probe(build_embedder(settings))
    report["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["model"] = settings.embedding_model
    report["configured_dimensions"] = settings.embedding_dimensions
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / (name + ".json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report:", target)
    print("Dimensions:", report["observed_dimensions"])
    print("Dense Top1 expected:", report["dense_top1_expected"])
    print("Fusion Top1 expected:", report["fusion_top1_expected"])
    print("Other-user results:", report["isolated_user_result_count"])


if __name__ == "__main__":
    cli_entrypoint(main)
