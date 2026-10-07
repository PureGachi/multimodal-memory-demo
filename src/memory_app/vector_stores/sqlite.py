import json
from memory_app.memory.models import Candidate


class SQLiteVectorStore:
    def __init__(self, store):
        self.store = store

    def insert(self, conn, user_id, vectors):
        conn.executemany("INSERT INTO vectors VALUES (?,?,?,?,?)", [
            (unit_id, user_id, version, order, json.dumps(vector))
            for unit_id, version, order, vector in vectors
        ])

    def search(self, user_id, version, queries, limit):
        with self.store.connect() as conn:
            rows = conn.execute("SELECT unit_id,value FROM vectors WHERE user_id=? AND version=?",
                                (user_id, version)).fetchall()
        scores = {}
        for unit_id, payload in rows:
            vector = json.loads(payload)
            for query in queries:
                if len(query) != len(vector):
                    continue
                score = sum(a * b for a, b in zip(query, vector))
                scores[unit_id] = max(scores.get(unit_id, -1), score)
        return [Candidate(id, score) for id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]
