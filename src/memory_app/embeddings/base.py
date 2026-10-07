from typing import Protocol


class Embedder(Protocol):
    version: str

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...
