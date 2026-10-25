"""Explicit, user-scoped maintenance. Stop the API before applying."""
import argparse
import json
import sqlite3
from memory_app.config import Settings
from memory_app.main import create_app
from memory_app.memory.backfill import backfill_images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually call analyzers and update records")
    parser.add_argument("--allow-remote", action="store_true", help="Allow sending images to configured paid API")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not (settings.data_dir / "memory.sqlite3").exists():
        parser.error("No existing memory database at MEMORY_DATA_DIR")
    if not args.apply:
        path = (settings.data_dir / "memory.sqlite3").resolve()
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT payload FROM units WHERE user_id=?", (args.user_id,)).fetchall()
        count = sum(bool(json.loads(row[0]).get("media_keys")) for row in rows)
        print(f"Preview: {count} image-containing messages. No model calls; use --apply to update.")
        return
    if settings.vision != "none" and not args.allow_remote:
        parser.error("Remote vision requires --allow-remote; image upload may incur fees")
    app = create_app(settings)
    service = app.state.service
    count = backfill_images(*service.dependencies, service.vision, args.user_id)
    print(f"Updated {count} messages. Earlier messages remain committed if a later one fails.")


if __name__ == "__main__":
    main()
