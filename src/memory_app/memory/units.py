def text_chunks(text: str, size: int = 400) -> list[str]:
    """Index all text in bounded chunks; each chunk points to its source unit."""
    return [text[i:i + size] for i in range(0, len(text), size)]
