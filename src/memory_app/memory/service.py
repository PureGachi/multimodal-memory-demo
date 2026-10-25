import logging
import time
from memory_app.memory.add import add_memory
from memory_app.memory.search import search_memory
from memory_app.memory.errors import StorageUnavailable

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, store, media, embedder, vectors, vision=None):
        self.dependencies = (store, media, embedder, vectors)
        self.vision = vision

    def add(self, payload):
        start = time.perf_counter()
        try:
            result = add_memory(payload, *self.dependencies, vision=self.vision)
        except OSError as exc:
            logger.error("add storage failure type=%s", type(exc).__name__)
            raise StorageUnavailable("Memory storage unavailable; retry the same Add request after recovery") from exc
        logger.info("add success elapsed_ms=%.1f messages=%d", (time.perf_counter() - start) * 1000,
                    len(payload["messages"]))
        return result

    def search(self, payload):
        start = time.perf_counter()
        try:
            result = search_memory(payload, *self.dependencies, vision=self.vision)
        except OSError as exc:
            logger.error("search storage failure type=%s", type(exc).__name__)
            raise StorageUnavailable("Memory evidence unavailable; check database and media storage") from exc
        logger.info("search success elapsed_ms=%.1f results=%d", (time.perf_counter() - start) * 1000,
                    len(result["data"]))
        return result
