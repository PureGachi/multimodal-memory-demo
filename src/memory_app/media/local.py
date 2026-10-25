import base64
import hashlib
import os
import tempfile
from pathlib import Path


class LocalMediaStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def key(self, user_id: str, raw: bytes) -> str:
        scope = hashlib.sha256(user_id.encode()).hexdigest()
        return f"{scope}/{hashlib.sha256(raw).hexdigest()}"

    def put(self, key: str, raw: bytes):
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return
        fd, temp = tempfile.mkstemp(dir=target.parent, prefix="pending-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.replace(temp, target)
            except PermissionError:
                # Windows can deny replacement when another writer has just
                # published/opened the same immutable content-addressed file.
                # Its presence already satisfies put; never suppress failure
                # when no target was successfully published.
                if not target.exists():
                    raise
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def uri(self, key: str, mime: str) -> str:
        raw = (self.root / key).read_bytes()
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}"

    def size(self, key: str) -> int:
        return (self.root / key).stat().st_size
