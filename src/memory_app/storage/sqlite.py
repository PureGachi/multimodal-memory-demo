import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from memory_app.memory.errors import Conflict, StorageUnavailable
from memory_app.memory.models import Unit


class SQLiteStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    user_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, PRIMARY KEY(user_id, request_id));
                CREATE TABLE IF NOT EXISTS units (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS units_user ON units(user_id);
                CREATE TABLE IF NOT EXISTS vectors (
                    unit_id TEXT NOT NULL REFERENCES units(id), user_id TEXT NOT NULL,
                    version TEXT NOT NULL, chunk_order INTEGER NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(unit_id, version, chunk_order));
                CREATE INDEX IF NOT EXISTS vectors_user ON vectors(user_id, version);
            """)

    @contextmanager
    def connect(self):
        conn = None
        try:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        except sqlite3.Error as exc:
            raise StorageUnavailable("Memory database unavailable; retry after storage recovery") from exc
        finally:
            if conn is not None:
                conn.close()

    def check_request(self, user_id, request_id, payload_hash, conn=None):
        if conn is None:
            with self.connect() as db:
                return self.check_request(user_id, request_id, payload_hash, db)
        row = conn.execute("SELECT payload_hash FROM requests WHERE user_id=? AND request_id=?",
                           (user_id, request_id)).fetchone()
        if row and row[0] != payload_hash:
            raise Conflict("request_id already exists with a different payload")
        return bool(row)

    def commit_add(self, user_id, request_id, payload_hash, units, vectors, vector_store):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self.check_request(user_id, request_id, payload_hash, conn):
                return
            for unit in units:
                conn.execute("INSERT INTO units VALUES (?,?,?)",
                             (unit.id, unit.user_id, json.dumps(asdict(unit), ensure_ascii=False)))
            vector_store.insert(conn, user_id, vectors)
            conn.execute("INSERT INTO requests VALUES (?,?,?)", (user_id, request_id, payload_hash))

    def list_units(self, user_id):
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM units WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
        return [Unit(**json.loads(row[0])) for row in rows]
