from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    text: str
    source: str
    position: int


def chunk_text(text: str, source: str, max_words: int = 180, overlap_words: int = 30) -> list[DocumentChunk]:
    """Deterministic baseline chunker with source and position metadata."""
    if max_words <= 0 or overlap_words < 0 or overlap_words >= max_words:
        raise ValueError("Require max_words > overlap_words >= 0")
    words = text.split()
    if not words:
        return []
    step = max_words - overlap_words
    chunks: list[DocumentChunk] = []
    for position, start in enumerate(range(0, len(words), step)):
        window = words[start : start + max_words]
        if not window:
            break
        chunks.append(
            DocumentChunk(
                chunk_id=f"{source}:{position}",
                text=" ".join(window),
                source=source,
                position=position,
            )
        )
        if start + max_words >= len(words):
            break
    return chunks

