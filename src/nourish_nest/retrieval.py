"""Offline BM25 candidates; no semantic or language-model claims."""

import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Protocol

from nourish_nest.knowledge_chunking import normalize
from nourish_nest.models import KnowledgeChunk

STOP_WORDS = frozenset(["a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "is", "are", "be", "how", "should", "can", "i", "my", "it", "with"])
K1 = Decimal("1.5")
B = Decimal("0.75")


def terms(text: str) -> list[str]:
    return [term for term in re.findall(r"\w+", normalize(text)) if term not in STOP_WORDS]


def stable_key(chunk: KnowledgeChunk):
    document = chunk.document
    return (normalize(document.title), normalize(document.source_name), document.source_uri or "",
            document.content_hash, str(document.household_id or ""), document.version,
            chunk.chunk_index, str(chunk.id))


@dataclass(frozen=True)
class Candidate:
    chunk: KnowledgeChunk
    retrieval_score: Decimal
    matched_terms: tuple[str, ...]
    term_scores: dict[str, Decimal]


class Retriever(Protocol):
    def retrieve(self, query: str, chunks: list[KnowledgeChunk], limit: int) -> list[Candidate]: ...


class LexicalRetriever:
    def retrieve(self, query, chunks, limit):
        query_terms = sorted(set(terms(query)))
        if not query_terms or not chunks:
            return []
        counts = [Counter(terms(" ".join([
            c.document.title, c.heading_path or "", c.normalized_content]))) for c in chunks]
        with localcontext() as context:
            context.prec = 28
            n = Decimal(len(chunks))
            average = Decimal(sum(sum(c.values()) for c in counts)) / n
            if not average:
                return []
            frequencies = {t: sum(t in c for c in counts) for t in query_terms}
            results = []
            for chunk, counter in zip(chunks, counts, strict=True):
                scores = {}
                for term in query_terms:
                    frequency = counter[term]
                    if frequency:
                        df = Decimal(frequencies[term])
                        idf = (1 + (n - df + Decimal("0.5")) / (df + Decimal("0.5"))).ln()
                        tf = Decimal(frequency)
                        length = Decimal(sum(counter.values()))
                        scores[term] = idf * tf * (K1 + 1) / (
                            tf + K1 * (1 - B + B * length / average))
                if scores:
                    results.append(Candidate(chunk, sum(scores.values(), Decimal(0)),
                                             tuple(sorted(scores)), scores))
            return sorted(results, key=lambda c: (-c.retrieval_score, stable_key(c.chunk)))[:limit]
