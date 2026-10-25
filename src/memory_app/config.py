import os
import math
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    embeddings: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text:latest"
    api_key: str = field(default="", repr=False)
    vision: str = "none"
    vision_base_url: str = ""
    vision_api_key: str = field(default="", repr=False)
    vision_model: str = ""
    vision_revision: str = "1"
    ocr: str = "none"
    tesseract_executable: str = "tesseract"
    ocr_languages: str = "eng"
    ocr_revision: str = "1"
    profile: str = "local"
    embedding_base_url: str = ""
    embedding_api_key: str = field(default="", repr=False)
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    embedding_revision: str = "1"
    vision_response_format: str = "json_schema"
    max_request_bytes: int = 64 * 1024 * 1024
    max_inflight: int = 2
    body_timeout_seconds: float = 30

    def validate(self):
        if type(self.max_request_bytes) is not int or self.max_request_bytes < 1:
            raise ValueError("MEMORY_MAX_REQUEST_BYTES must be a positive integer")
        if type(self.max_inflight) is not int or not 1 <= self.max_inflight <= 64:
            raise ValueError("MEMORY_MAX_INFLIGHT must be between 1 and 64")
        if type(self.body_timeout_seconds) not in (int, float) or not math.isfinite(self.body_timeout_seconds) or self.body_timeout_seconds <= 0:
            raise ValueError("MEMORY_BODY_TIMEOUT_SECONDS must be finite and positive")
        if self.vision_response_format not in {"json_schema", "json_object"}:
            raise ValueError("VISION_RESPONSE_FORMAT must be json_schema or json_object")
        if self.profile not in {"local", "competition"}:
            raise ValueError("MEMORY_PROFILE must be local or competition")
        if self.embeddings not in {"ollama", "none", "compatible"}:
            raise ValueError("MEMORY_EMBEDDINGS must be ollama, none or compatible")
        if self.profile == "competition":
            if self.embeddings != "compatible" or self.embedding_model != "text-embedding-v4":
                raise ValueError("Competition profile requires compatible text-embedding-v4 embeddings")
            if self.vision not in {"none", "compatible"} or (
                    self.vision == "compatible" and self.vision_model != "gpt-4o-mini"):
                raise ValueError("Competition profile requires gpt-4o-mini for enabled vision")
            if self.ocr != "none":
                raise ValueError("Competition profile disables local OCR pending organizer clarification")
            if not self.api_key.strip():
                raise ValueError("Competition profile requires MEMORY_API_KEY for service authentication")

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env"):
        """Current-directory .env < process environment < explicit Settings.

        Do not search parent directories or mutate os.environ. Pass None to
        ignore the file (useful for isolated tests and environment-only callers).
        Values are literal: no shell execution or ${...} interpolation.
        """
        values = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False) if env_file is not None else {}
        env = {**{key: value for key, value in values.items() if value is not None}, **os.environ}
        profile = env.get("MEMORY_PROFILE", "local")
        competition = profile == "competition"
        mode = env.get("MEMORY_EMBEDDINGS", "compatible" if competition else "ollama")
        settings = cls(data_dir=Path(env.get("MEMORY_DATA_DIR", "data/competition" if competition else "data")), embeddings=mode,
                   ollama_url=env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
                   embed_model=env.get("OLLAMA_EMBED_MODEL", "nomic-embed-text:latest"),
                   api_key=env.get("MEMORY_API_KEY", ""),
                   vision=env.get("MEMORY_VISION", "compatible" if competition else "none"),
                   vision_base_url=env.get("VISION_BASE_URL", "https://api.openai.com/v1" if competition else ""),
                   vision_api_key=env.get("VISION_API_KEY", ""),
                   vision_model=env.get("VISION_MODEL", "gpt-4o-mini" if competition else ""),
                   vision_revision=env.get("VISION_REVISION", "1"),
                   ocr=env.get("MEMORY_OCR", "none"),
                   tesseract_executable=env.get("TESSERACT_EXECUTABLE", "tesseract"),
                   ocr_languages=env.get("OCR_LANGUAGES", "eng"),
                   ocr_revision=env.get("OCR_REVISION", "1"), profile=profile,
                   embedding_base_url=env.get("EMBEDDING_BASE_URL", ""),
                   embedding_api_key=env.get("EMBEDDING_API_KEY", ""),
                   embedding_model=env.get("EMBEDDING_MODEL", "text-embedding-v4"),
                   embedding_dimensions=int(env.get("EMBEDDING_DIMENSIONS", "1024")),
                   embedding_revision=env.get("EMBEDDING_REVISION", "1"),
                   vision_response_format=env.get("VISION_RESPONSE_FORMAT", "json_schema"),
                   max_request_bytes=int(env.get("MEMORY_MAX_REQUEST_BYTES", str(64 * 1024 * 1024))),
                   max_inflight=int(env.get("MEMORY_MAX_INFLIGHT", "2")),
                   body_timeout_seconds=float(env.get("MEMORY_BODY_TIMEOUT_SECONDS", "30")))
        settings.validate()
        return settings
