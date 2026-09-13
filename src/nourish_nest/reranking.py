"""Explainable local reranking; weights are versioned application policy."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Protocol

from nourish_nest.knowledge_types import SourceType
from nourish_nest.retrieval import Candidate, stable_key, terms

WEIGHTS = {"lexical": Decimal(1), "overlap": Decimal(2), "title": Decimal(1),
           "heading": Decimal(1), "phrase": Decimal("0.5"), "source": Decimal("0.1")}
SOURCE_PRIORITY = {SourceType.DOCUMENTATION: Decimal(1), SourceType.FOOD_SAFETY: Decimal(1),
                   SourceType.NUTRITION: Decimal("0.8"), SourceType.RECIPE: Decimal("0.5"),
                   SourceType.USER: Decimal(0)}


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    score: Decimal
    components: dict[str, Decimal]


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[Candidate], top_k: int
               ) -> list[RankedCandidate]: ...


class DeterministicReranker:
    def rerank(self, query, candidates, top_k):
        query_terms = set(terms(query))
        if not query_terms:
            return []
        results = []
        with localcontext() as context:
            context.prec = 28
            denominator = Decimal(len(query_terms))
            for candidate in candidates:
                chunk = candidate.chunk
                components = {
                    "lexical": candidate.retrieval_score,
                    "overlap": Decimal(len(candidate.matched_terms)) / denominator,
                    "title": Decimal(len(query_terms & set(terms(chunk.document.title)))) / denominator,
                    "heading": Decimal(len(query_terms & set(terms(chunk.heading_path or ""))))
                    / denominator,
                    "phrase": Decimal(int(" " + " ".join(terms(query)) + " " in
                                          " " + " ".join(terms(chunk.content)) + " ")),
                    "source": SOURCE_PRIORITY[chunk.document.source_type],
                }
                score = sum((WEIGHTS[k] * value for k, value in components.items()), Decimal(0))
                components.update({f"term:{k}": v for k, v in candidate.term_scores.items()})
                results.append(RankedCandidate(candidate, score, components))
        return sorted(results, key=lambda r: (-r.score, stable_key(r.candidate.chunk)))[:top_k]
