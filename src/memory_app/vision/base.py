from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ImageAnalysis:
    description: str = ""
    visible_text: str = ""
    contextual_description: str = ""

    def __post_init__(self):
        for value in (self.description, self.visible_text, self.contextual_description):
            if not isinstance(value, str) or len(value) > 16000:
                raise ValueError("Image analysis fields must be strings of at most 16000 characters")


class ImageAnalyzer(Protocol):
    version: str

    def analyze(self, raw: bytes, mime: str) -> ImageAnalysis: ...
