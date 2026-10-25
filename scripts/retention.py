"""Run from project root; .env and model clients are never loaded."""
from memory_app.governance.retention import main

if __name__ == "__main__":
    raise SystemExit(main())
