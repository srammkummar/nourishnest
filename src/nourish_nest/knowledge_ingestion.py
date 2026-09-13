"""Explicit, offline admin ingestion. No public ingestion endpoint."""

import argparse
import json
import re
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nourish_nest.config import get_settings
from nourish_nest.database import SessionLocal
from nourish_nest.knowledge_chunking import Chunker, StructureChunker
from nourish_nest.knowledge_repositories import KnowledgeRepository
from nourish_nest.knowledge_schemas import DocumentInput, IngestionManifest
from nourish_nest.services import ConflictError, NotFoundError

MAX_FILE_BYTES = 1_048_576


def require_local_path(path: Path) -> None:
    value = str(path)
    if (value.startswith(("\\\\", "//")) or "://" in value
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]{1,}:", value)):
        raise ValueError("Remote URLs and network share paths are not supported")


def read_local(path: Path, roots: list[Path]) -> str:
    require_local_path(path)
    for root in roots:
        require_local_path(root)
    resolved = path.resolve(strict=True)
    require_local_path(resolved)
    if not any(resolved.is_relative_to(root.resolve(strict=True)) for root in roots):
        raise ValueError("Document is outside approved ingestion roots")
    if resolved.suffix.casefold() not in {".txt", ".md"} or not resolved.is_file():
        raise ValueError("Only local UTF-8 .txt and .md files are supported")
    with resolved.open("rb") as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Document exceeds 1 MiB limit")
    content = raw.decode("utf-8-sig")
    if "\x00" in content:
        raise ValueError("Binary content is not supported")
    # Defense in depth only: the administrator remains responsible for reviewing evidence.
    if re.search(r"-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}|"
                 r"(?i:api[_ -]?key|password|secret)\s*[:=]\s*\S+", content):
        raise ValueError("Document appears to contain credentials; remove them before ingestion")
    return content


def ingest_document(session: Session, data: DocumentInput, content: str,
                    chunker: Chunker | None = None):
    chunks = (chunker or StructureChunker()).chunk(data.title, content)
    return KnowledgeRepository(session).ingest(data, content, chunks)


def ingest_manifest(session: Session, manifest_path: Path, roots: list[Path],
                    chunker: Chunker | None = None) -> list[dict]:
    if not roots:
        raise ValueError("Configure APP_KNOWLEDGE_INGESTION_ROOTS before ingestion")
    require_local_path(manifest_path)
    with manifest_path.open("rb") as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Manifest exceeds 1 MiB limit")
    manifest = IngestionManifest.model_validate_json(raw)
    # Validate all files before starting the atomic manifest transaction.
    prepared = []
    for entry in manifest.documents:
        require_local_path(Path(entry.path))
        prepared.append((entry, read_local(manifest_path.parent / entry.path, roots)))
    results = []
    with session.begin():
        for entry, content in prepared:
            data = DocumentInput.model_validate(entry.model_dump(exclude={"path"}))
            document, action = ingest_document(session, data, content, chunker)
            results.append({"document_id": str(document.id), "action": action,
                            "version": document.version, "chunks": len(document.chunks)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    try:
        chunker = StructureChunker(settings.knowledge_chunk_target, settings.knowledge_chunk_overlap)
        with SessionLocal() as session:
            results = ingest_manifest(session, args.manifest,
                                      [Path(p) for p in settings.knowledge_ingestion_roots], chunker)
        print(json.dumps({"status": "ok", "documents": results}, indent=2))
        return 0
    except (ValueError, OSError, SQLAlchemyError, NotFoundError, ConflictError) as exc:
        # ValidationError/SQL exceptions may contain document text: do not print exception details.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "message": "Ingestion rejected; check manifest, roots, ownership and schema."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
