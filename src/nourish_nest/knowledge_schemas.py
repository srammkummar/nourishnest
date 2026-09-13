"""Strict API and admin contracts; no ORM types exposed."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nourish_nest.knowledge_types import SourceType, Visibility


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300)
    source_name: str = Field(min_length=1, max_length=300)
    source_uri: str | None = Field(default=None, max_length=2000)
    source_type: SourceType
    visibility: Visibility
    household_id: UUID | None = None

    @model_validator(mode="after")
    def ownership(self):
        if (self.visibility == Visibility.HOUSEHOLD) != (self.household_id is not None):
            raise ValueError("Household visibility requires an ID; global visibility forbids it")
        return self


class ManifestEntry(DocumentInput):
    path: str = Field(min_length=1, max_length=2000)


class IngestionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[ManifestEntry] = Field(min_length=1, max_length=100)


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    candidate_count: int = Field(default=20, ge=1, le=100, strict=True)
    source_types: list[SourceType] | None = Field(default=None, min_length=1, max_length=5)

    @model_validator(mode="after")
    def candidate_limit(self):
        if self.candidate_count < self.top_k:
            raise ValueError("candidate_count must be at least top_k")
        return self


class Citation(BaseModel):
    rank: int
    document_id: UUID
    chunk_id: UUID
    document_title: str
    source_name: str
    source_uri: str | None
    heading_path: str | None
    chunk_index: int
    excerpt: str
    retrieval_score: Decimal
    reranking_score: Decimal
    matched_terms: list[str]
    score_components: dict[str, Decimal]


class KnowledgeWarning(BaseModel):
    code: str
    message: str
    chunk_id: UUID | None = None


class RetrievalResponse(BaseModel):
    query: str
    results: list[Citation]
    warnings: list[KnowledgeWarning]
    retrieval_version: str = "knowledge-retrieval-v1"
