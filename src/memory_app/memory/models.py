from dataclasses import dataclass, field


@dataclass
class Unit:
    id: str
    user_id: str
    session_id: str
    role: str
    timestamp: int | None
    content: str | list[dict]
    text: str
    created_at: str
    media_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    id: str
    score: float
