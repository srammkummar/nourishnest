"""Structure-aware, word-bounded chunking without external tokenizers."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def canonical_content(value: str) -> str:
    return "\n".join(" ".join(line.split()) for line in value.splitlines()).strip()


@dataclass(frozen=True)
class Chunk:
    title: str
    heading_path: str | None
    content: str
    content_hash: str
    word_count: int


class Chunker(Protocol):
    def chunk(self, title: str, content: str) -> list[Chunk]: ...


class StructureChunker:
    def __init__(self, target: int = 180, overlap: int = 30):
        if not 0 <= overlap < target:
            raise ValueError("Require target > overlap >= 0")
        self.target, self.overlap = target, overlap

    def chunk(self, title: str, content: str) -> list[Chunk]:
        content = canonical_content(content)
        if not content:
            raise ValueError("Document content must not be empty")
        sections: list[tuple[str | None, str]] = []
        headings: list[tuple[int, str]] = []
        lines: list[str] = []

        def flush_section():
            if lines:
                sections.append((" / ".join(h[1] for h in headings) or None, "\n".join(lines)))
                lines.clear()

        for line in content.splitlines():
            heading = re.fullmatch(r"(#{1,6})\s+(.+?)\s*#*", line)
            if heading:
                flush_section()
                level, name = len(heading[1]), heading[2]
                headings = [h for h in headings if h[0] < level] + [(level, name)]
            else:
                lines.append(line)
        flush_section()
        result: list[Chunk] = []

        def emit(path, words):
            text = " ".join(words)
            result.append(Chunk(title, path, text, digest(text), len(words)))

        for path, section in sections:
            pending: list[str] = []
            for paragraph in re.split(r"\n\s*\n", section):
                words = paragraph.split()
                if not words:
                    continue
                if len(words) > self.target:
                    if pending:
                        emit(path, pending)
                        pending = []
                    start = 0
                    while start < len(words):
                        window = words[start:start + self.target]
                        emit(path, window)
                        if start + self.target >= len(words):
                            break
                        start += self.target - self.overlap
                else:
                    if len(pending) + len(words) > self.target:
                        emit(path, pending)
                        pending = []
                    pending.extend(words)
            if pending:
                emit(path, pending)
        if not result:
            raise ValueError("Document must include content beyond headings")
        return result
