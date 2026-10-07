import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    embeddings: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text:latest"
    api_key: str = ""

    @classmethod
    def from_env(cls):
        mode = os.getenv("MEMORY_EMBEDDINGS", "ollama")
        if mode not in {"ollama", "none"}:
            raise ValueError("MEMORY_EMBEDDINGS must be ollama or none")
        return cls(Path(os.getenv("MEMORY_DATA_DIR", "data")), mode,
                   os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                   os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text:latest"),
                   os.getenv("MEMORY_API_KEY", ""))
