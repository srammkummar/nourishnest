"""Scoped persistence only; transaction ownership belongs to the ingestion caller."""

import json
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from nourish_nest.knowledge_chunking import Chunk, canonical_content, digest, normalize
from nourish_nest.knowledge_schemas import DocumentInput
from nourish_nest.knowledge_types import DocumentStatus, SourceType, Visibility
from nourish_nest.models import Household, KnowledgeChunk, KnowledgeDocument
from nourish_nest.services import ConflictError, NotFoundError


class KnowledgeRepository:
    def __init__(self, session: Session):
        self.session = session

    def require_household(self, household_id: UUID):
        if self.session.get(Household, household_id) is None:
            raise NotFoundError("Household not found")

    def visible_chunks(self, household_id: UUID, source_types: list[SourceType] | None):
        scope = or_(
            (KnowledgeDocument.visibility == Visibility.GLOBAL)
            & KnowledgeDocument.household_id.is_(None),
            (KnowledgeDocument.visibility == Visibility.HOUSEHOLD)
            & (KnowledgeDocument.household_id == household_id),
        )
        statement = select(KnowledgeChunk).join(KnowledgeChunk.document).options(
            joinedload(KnowledgeChunk.document)).where(
                scope, KnowledgeDocument.status == DocumentStatus.ACTIVE)
        if source_types:
            statement = statement.where(KnowledgeDocument.source_type.in_(source_types))
        return list(self.session.scalars(statement))

    def ingest(self, data: DocumentInput, content: str, chunks: list[Chunk]):
        if data.household_id is not None:
            self.require_household(data.household_id)
        scope = str(data.household_id) if data.household_id else "global"
        content_hash = digest(canonical_content(content))
        scoped_hash = digest(scope + ":" + content_hash)
        lineage = digest(json.dumps([scope, data.source_name, data.source_uri or data.title]))
        previous = self.session.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.lineage_key == lineage,
            KnowledgeDocument.status == DocumentStatus.ACTIVE).with_for_update())
        existing = self.session.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.scoped_hash == scoped_hash,
            KnowledgeDocument.status == DocumentStatus.ACTIVE))
        if existing:
            if previous and previous.id != existing.id:
                raise ConflictError("Changed content duplicates another active source; review lineage")
            return existing, "unchanged"
        version = (self.session.scalar(select(func.max(KnowledgeDocument.version)).where(
            KnowledgeDocument.lineage_key == lineage)) or 0) + 1
        if previous:
            previous.status = DocumentStatus.SUPERSEDED
            self.session.flush()
        document = KnowledgeDocument(**data.model_dump(), content_hash=content_hash,
                                     scoped_hash=scoped_hash, lineage_key=lineage, version=version)
        document.chunks = [KnowledgeChunk(
            chunk_index=index, heading_path=chunk.heading_path, content=chunk.content,
            normalized_content=normalize(chunk.content), word_count=chunk.word_count,
            content_hash=chunk.content_hash,
            metadata_json={"title": chunk.title, "chunking_version": "structure-v1"},
        ) for index, chunk in enumerate(chunks)]
        self.session.add(document)
        self.session.flush()
        return document, "revised" if previous else "created"
