"""Per-run persistent evaluation state and successful-model cache. No retries."""
from dataclasses import asdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile

from memory_app.errors import error_feedback
from memory_app.memory.errors import CheckpointError
from memory_app.vision.base import ImageAnalysis


PROTOCOL = "gallery-checkpoint-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pending-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class EvaluationCheckpoint:
    def __init__(self, root, identity, *, resume=False, options=None):
        self.root = Path(root).resolve()
        self.fingerprint = digest([PROTOCOL, identity])
        self.resume = resume
        self.options = options or {}
        self.state = {}
        self.lock = None
        self.initialized = False

    def __enter__(self):
        if self.resume and not (self.root / "state.json").is_file():
            raise CheckpointError("Resume requires an existing state.json")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / ".lock").open("a+b")
        try:
            self.lock.seek(0, 2)
            if self.lock.tell() == 0:
                self.lock.write(b"0")
                self.lock.flush()
            self.lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            self.lock = None
            raise CheckpointError("Checkpoint is already in use") from None
        try:
            path = self.root / "state.json"
            if self.resume:
                try:
                    self.state = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    raise CheckpointError("Checkpoint state cannot be read") from None
                if not isinstance(self.state, dict):
                    raise CheckpointError("Invalid checkpoint state")
                if (self.state.get("protocol") != PROTOCOL or self.state.get("fingerprint") != self.fingerprint):
                    raise CheckpointError("Dataset, model or evaluation parameters changed")
                if not (self.root / "cache.sqlite3").is_file():
                    raise CheckpointError("Checkpoint cache is missing")
            elif path.exists():
                raise CheckpointError("Checkpoint exists; explicitly resume or use a new directory")
            else:
                self.state = {"protocol": PROTOCOL, "fingerprint": self.fingerprint,
                              "created_at_utc": datetime.now(timezone.utc).isoformat(),
                              "status": "new", "stage": "prepare", "indexed_rounds": 0,
                              "completed_questions": 0, "options": self.options}
            with self.connect() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS model_cache (
                        kind TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
                        PRIMARY KEY(kind,key));
                    CREATE TABLE IF NOT EXISTS questions (number INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                """)
            self.initialized = True
            self.progress(status="running", error=None)
            return self
        except BaseException:
            self._unlock()
            raise

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.root / "cache.sqlite3", timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def progress(self, **updates):
        self.state.update(updates)
        self.state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_json(self.root / "state.json", self.state)

    def cached(self, kind, key):
        try:
            with self.connect() as conn:
                row = conn.execute("SELECT payload FROM model_cache WHERE kind=? AND key=?", (kind, key)).fetchone()
            if row is None:
                return None
            value = json.loads(row[0])
            if value is None:
                raise CheckpointError("Invalid null cache entry")
            return value
        except (ValueError, sqlite3.Error):
            raise CheckpointError("Invalid model cache") from None

    def save_cache(self, kind, key, value):
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO model_cache VALUES (?,?,?)",
                         (kind, key, json.dumps(value, ensure_ascii=False)))

    def question(self, number):
        try:
            with self.connect() as conn:
                row = conn.execute("SELECT payload FROM questions WHERE number=?", (number,)).fetchone()
            if row is None:
                return None
            value = json.loads(row[0])
            if (not isinstance(value, dict) or value.get("number") != number
                    or not isinstance(value.get("channels"), dict)):
                raise CheckpointError("Invalid saved question")
            return value
        except (ValueError, sqlite3.Error):
            raise CheckpointError("Invalid question checkpoint") from None

    def save_question(self, value):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO questions VALUES (?,?)",
                         (value["number"], json.dumps(value, ensure_ascii=False)))

    def _unlock(self):
        if self.lock:
            # Closing releases OS locks, including on process termination. No PID guessing.
            self.lock.close()
            self.lock = None

    def __exit__(self, kind, exc, tb):
        try:
            if exc is not None and self.initialized:
                self.progress(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                              error=error_feedback(exc))
        finally:
            self._unlock()

    def embedder(self, inner):
        return CachedEmbedder(inner, self) if inner else None

    def vision(self, inner):
        return CachedVision(inner, self) if inner else None


class CachedEmbedder:
    def __init__(self, inner, checkpoint):
        self.inner, self.checkpoint, self.version = inner, checkpoint, inner.version

    def _validate(self, vector):
        expected = getattr(self.inner, "dimensions", None)
        if (not isinstance(vector, list) or not vector or (expected is not None and len(vector) != expected)
                or not all(type(x) in (int, float) and math.isfinite(x) for x in vector)
                or not 0 < math.hypot(*vector) < math.inf):
            raise CheckpointError("Invalid cached embedding")

    def embed(self, texts, *, query=False):
        keys = [digest([self.version, query, text]) for text in texts]
        values, missing = {}, {}
        for key, text in zip(keys, texts):
            if key in values or key in missing:
                continue
            value = self.checkpoint.cached("embedding", key)
            if value is None:
                missing[key] = text
            else:
                self._validate(value)
                values[key] = value
        items = list(missing.items())
        # Persist each successful batch immediately, even if the next batch fails.
        for offset in range(0, len(items), 10):
            batch = items[offset:offset + 10]
            vectors = self.inner.embed([text for _, text in batch], query=query)
            if len(vectors) != len(batch):
                raise CheckpointError("Embedding count mismatch")
            for (key, _), vector in zip(batch, vectors):
                self._validate(vector)
                self.checkpoint.save_cache("embedding", key, vector)
                values[key] = vector
        result = [values[key] for key in keys]
        if len({len(vector) for vector in result}) > 1:
            raise CheckpointError("Embedding dimensions changed")
        return result


class CachedVision:
    def __init__(self, inner, checkpoint):
        self.inner, self.checkpoint, self.version = inner, checkpoint, inner.version
        self.supports_context = getattr(inner, "supports_context", False)
        self.provenance = getattr(inner, "provenance", [{"version": inner.version}])

    def analyze(self, raw, mime):
        return self._analyze(raw, mime, None)

    def analyze_with_context(self, raw, mime, context):
        return self._analyze(raw, mime, context)

    def _analyze(self, raw, mime, context):
        from memory_app.vision.pipeline import analysis_version
        key = digest([analysis_version(self, context), mime, hashlib.sha256(raw).hexdigest()])
        value = self.checkpoint.cached("vision", key)
        if value is not None:
            try:
                return ImageAnalysis(**value)
            except (ValueError, TypeError):
                raise CheckpointError("Invalid cached image analysis") from None
        result = (self.inner.analyze_with_context(raw, mime, context)
                  if context and self.supports_context else self.inner.analyze(raw, mime))
        self.checkpoint.save_cache("vision", key, asdict(result))
        return result
