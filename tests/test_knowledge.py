import json
import os
import subprocess
import sys
from decimal import Decimal, localcontext
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from alembic import command
from nourish_nest.api import app
from nourish_nest.config import get_settings
from nourish_nest.database import Base, get_db
from nourish_nest.evals.knowledge_retrieval import (
    DATASET,
    HOUSEHOLDS,
    evaluate_case,
    run_evaluations,
    seed,
)
from nourish_nest.knowledge_chunking import StructureChunker, digest
from nourish_nest.knowledge_ingestion import (
    ingest_document,
    ingest_manifest,
    read_local,
    require_local_path,
)
from nourish_nest.knowledge_schemas import DocumentInput, RetrievalRequest
from nourish_nest.knowledge_services import KnowledgeService
from nourish_nest.models import Household, KnowledgeChunk, KnowledgeDocument
from nourish_nest.reranking import WEIGHTS, DeterministicReranker
from nourish_nest.retrieval import LexicalRetriever

GOLDEN = json.loads(DATASET.read_text(encoding="utf-8"))


@pytest.fixture
def knowledge_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}",
                           connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session, GOLDEN)
    try:
        yield engine
    finally:
        engine.dispose()


def data(**overrides):
    return DocumentInput.model_validate({"title": "Revision test", "source_name": "Original test",
                                        "source_type": "user_document", "visibility": "global",
                                        **overrides})


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["id"])
def test_golden(knowledge_db, case):
    with Session(knowledge_db) as session:
        result = evaluate_case(session, case)
    assert result["rank"] == 1 if case["expected"] else result["no_answer_accuracy"]
    for key in ("citation_correctness", "household_isolation_accuracy",
                "deterministic_repeat_accuracy", "source_filter_accuracy", "extra_expectations"):
        assert result[key]


def test_chunk_structure_hashes_and_paragraph_boundaries():
    chunker = StructureChunker(8, 2)
    content = "# Parent\n## Child\nOne two three.\n\nFour five six.\n\nSeven eight nine."
    chunks = chunker.chunk("Title", content)
    assert chunks == chunker.chunk("Title", content)
    assert [c.content for c in chunks] == ["One two three. Four five six.", "Seven eight nine."]
    assert all(c.heading_path == "Parent / Child" and c.title == "Title" for c in chunks)
    assert all(c.content_hash == digest(c.content) for c in chunks)


@pytest.mark.parametrize("overlap", [0, 1, 3])
def test_long_paragraph_overlap(overlap):
    words = [f"word{i}" for i in range(23)]
    chunks = StructureChunker(8, overlap).chunk("Title", " ".join(words))
    assert all(c.word_count <= 8 for c in chunks)
    reconstructed = chunks[0].content.split()
    for previous, current in pairwise(chunks):
        if overlap:
            assert previous.content.split()[-overlap:] == current.content.split()[:overlap]
        reconstructed.extend(current.content.split()[overlap:])
    assert reconstructed == words


@pytest.mark.parametrize("content", ["", " \n\t ", "# Heading only"])
def test_empty_rejected(content):
    with pytest.raises(ValueError):
        StructureChunker().chunk("Title", content)


@pytest.mark.parametrize("target,overlap", [(0, 0), (3, 3), (3, -1)])
def test_invalid_chunk_config(target, overlap):
    with pytest.raises(ValueError):
        StructureChunker(target, overlap)


def test_revision_deduplication_rollback_and_scope(knowledge_db):
    with Session(knowledge_db) as session:
        first, action = ingest_document(session, data(), "Original marker.")
        session.commit()
        original_id = first.id
        duplicate, action = ingest_document(session, data(title="Copy"), "Original marker.")
        assert duplicate.id == original_id and action == "unchanged"
        private, _ = ingest_document(session, data(visibility="household", household_id=HOUSEHOLDS['a']),
                                      "Original marker.")
        assert private.id != original_id
        session.commit()
        second, action = ingest_document(session, data(), "Revised marker.")
        assert action == "revised" and second.version == 2 and first.status == "superseded"
        session.rollback()
        assert session.get(KnowledgeDocument, original_id).status == "active"
        second, _ = ingest_document(session, data(), "Revised marker.")
        session.commit()
        assert first.chunks[0].content == "Original marker."
        results = KnowledgeService(session).retrieve(HOUSEHOLDS['b'], RetrievalRequest(query="marker"))
        assert [r.document_id for r in results.results] == [second.id]


@pytest.mark.parametrize("changes", [
    {"visibility": "household", "household_id": None},
    {"visibility": "global", "household_id": HOUSEHOLDS['a']},
    {"status": "invalid"}, {"source_type": "invalid"}, {"version": 0},
    {"content_hash": "short"}, {"title": " "}, {"source_name": " "},
])
def test_document_database_constraints(knowledge_db, changes):
    with Session(knowledge_db) as session:
        doc, _ = ingest_document(session, data(), "Constraint fixture.")
        for key, value in changes.items():
            setattr(doc, key, value)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


@pytest.mark.parametrize("changes", [{"word_count": 0}, {"chunk_index": -1},
                                      {"content": " "}, {"content_hash": "bad"}])
def test_chunk_database_constraints(knowledge_db, changes):
    with Session(knowledge_db) as session:
        chunk = session.scalar(select(KnowledgeChunk))
        for key, value in changes.items():
            setattr(chunk, key, value)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_optimistic_version_and_cascade(knowledge_db):
    with Session(knowledge_db) as a, Session(knowledge_db) as b:
        doc_a = a.scalar(select(KnowledgeDocument).where(KnowledgeDocument.household_id == HOUSEHOLDS['a']))
        doc_b = b.get(KnowledgeDocument, doc_a.id)
        doc_a.status = "failed"
        a.commit()
        doc_b.status = "superseded"
        with pytest.raises(StaleDataError):
            b.commit()
        b.rollback()
        chunk_id = doc_a.chunks[0].id
        a.delete(a.get(Household, HOUSEHOLDS['a']))
        a.commit()
        a.expire_all()
        assert a.get(KnowledgeChunk, chunk_id) is None


def test_failed_excluded_and_scores(knowledge_db):
    with Session(knowledge_db) as session:
        chunks = list(session.scalars(select(KnowledgeChunk)))
        candidates = LexicalRetriever().retrieve("rice storage", chunks, 2)
        assert len(candidates) == 1
        assert candidates[0].retrieval_score == sum(candidates[0].term_scores.values())
        ranked = DeterministicReranker().rerank("rice storage", candidates, 1)
        assert ranked[0].score == sum(WEIGHTS[k] * ranked[0].components[k] for k in WEIGHTS)
        doc = candidates[0].chunk.document
        doc.status = "failed"
        session.commit()
        assert not KnowledgeService(session).retrieve(HOUSEHOLDS['a'], RetrievalRequest(query="rice")).results


def test_decimal_context_independence(knowledge_db):
    with Session(knowledge_db) as session:
        request = RetrievalRequest(query="rice storage")
        expected = KnowledgeService(session).retrieve(HOUSEHOLDS['a'], request)
        with localcontext() as context:
            context.prec = 8
            assert KnowledgeService(session).retrieve(HOUSEHOLDS['a'], request) == expected


@pytest.mark.parametrize("filename,content", [("x.html", "hello"), ("x.md", ""),
                                              ("x.md", "password=privatevalue"),
                                              ("x.md", "\x00binary")])
def test_ingestion_rejection(tmp_path, knowledge_db, filename, content):
    (tmp_path / filename).write_text(content, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"documents": [{**data().model_dump(mode="json"), "path": filename}]}))
    with Session(knowledge_db) as session, pytest.raises(ValueError):
        ingest_manifest(session, manifest, [tmp_path])


def test_roots_and_atomic_manifest(tmp_path, knowledge_db):
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("Not approved.")
    with pytest.raises(ValueError, match="outside"):
        read_local(approved / ".." / "outside.md", [approved])
    (approved / "good.md").write_text("Atomic ingestion fixture.")
    entry = {**data().model_dump(mode="json"), "path": "good.md"}
    manifest = approved / "manifest.json"
    manifest.write_text(json.dumps({"documents": [entry, {**entry, "visibility": "household",
                                                         "household_id": str(uuid4())}]}))
    with Session(knowledge_db) as session:
        before = len(list(session.scalars(select(KnowledgeDocument))))
        session.rollback()
        with pytest.raises(LookupError):
            ingest_manifest(session, manifest, [approved])
        assert len(list(session.scalars(select(KnowledgeDocument)))) == before
        session.rollback()
        manifest.write_text(json.dumps({"documents": [entry]}))
        assert ingest_manifest(session, manifest, [approved])[0]["action"] == "created"
        assert ingest_manifest(session, manifest, [approved])[0]["action"] == "unchanged"


@pytest.fixture
def client(knowledge_db):
    def db():
        with Session(knowledge_db) as session:
            yield session
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("payload", [
    {"query": " "}, {"query": "x" * 1001}, {"query": "rice", "top_k": 0},
    {"query": "rice", "top_k": 21}, {"query": "rice", "candidate_count": 101},
    {"query": "rice", "candidate_count": 2}, {"query": "rice", "source_types": ["unknown"]},
    {"query": "rice", "source_types": []}, {"query": "rice", "top_k": True},
    {"query": "rice", "tool": "delete"},
])
def test_api_validation_and_request_id(client, payload):
    response = client.post(f"/v1/households/{HOUSEHOLDS['a']}/knowledge/retrieve", json=payload,
                           headers={"x-request-id": "knowledge-test"})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["request_id"] == response.headers["x-request-id"] == "knowledge-test"


def test_api_success_missing_and_read_only(client, knowledge_db):
    writes = []
    def statements(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(statement)
    event.listen(knowledge_db, "before_cursor_execute", statements)
    try:
        with Session(knowledge_db) as session:
            before = {t.name: list(session.execute(select(t))) for t in Base.metadata.sorted_tables}
        response = client.post(f"/v1/households/{HOUSEHOLDS['a']}/knowledge/retrieve",
                               json={"query": "rice"})
        assert response.status_code == 200 and response.headers["x-request-id"]
        assert Decimal(response.json()["results"][0]["retrieval_score"]) > 0
        missing = client.post(f"/v1/households/{uuid4()}/knowledge/retrieve", json={"query": "rice"})
        assert missing.status_code == 404 and missing.json()["request_id"]
        with Session(knowledge_db) as session:
            session.add(Household(name="Must not flush", timezone="UTC", currency="USD"))
            KnowledgeService(session).retrieve(HOUSEHOLDS['a'], RetrievalRequest(query="saffronmarker"))
            assert session.new
        with Session(knowledge_db) as session:
            assert before == {t.name: list(session.execute(select(t))) for t in Base.metadata.sorted_tables}
        assert not writes
    finally:
        event.remove(knowledge_db, "before_cursor_execute", statements)


def test_migration_round_trip(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("APP_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "20260909_0009")
        command.upgrade(config, "head")
        engine = create_engine(url)
        assert {"knowledge_documents", "knowledge_chunks"} <= set(inspect(engine).get_table_names())
        command.downgrade(config, "-1")
        assert "knowledge_documents" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260913_0010"
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_evaluation_reports_and_failure_threshold(tmp_path, monkeypatch):
    report = run_evaluations(tmp_path)
    assert report["passed"] and report["case_count"] >= 30
    assert (tmp_path / "report.md").exists()
    monkeypatch.setitem(__import__("nourish_nest.evals.knowledge_retrieval", fromlist=["THRESHOLDS"])
                        .THRESHOLDS, "hit_at_1", Decimal("1.1"))
    assert not run_evaluations(tmp_path)["passed"]


def test_evaluation_command(tmp_path):
    result = subprocess.run([sys.executable, "-m", "nourish_nest.evals.knowledge_retrieval",
                             "--output", str(tmp_path)], capture_output=True, text=True,
                            env={**os.environ, "UV_OFFLINE": "1"}, timeout=60, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["https://example.org/x.md", "http://example.org/x.md",
                                   "\\\\server\\share\\x.md", "//server/share/x.md"])
def test_remote_paths_rejected_before_io(value):
    with pytest.raises(ValueError, match="Remote"):
        require_local_path(Path(value))


def test_duplicate_revision_conflict(knowledge_db):
    from nourish_nest.services import ConflictError
    with Session(knowledge_db) as session:
        first, _ = ingest_document(session, data(), "First text.")
        ingest_document(session, data(title="Other source"), "Second text.")
        session.commit()
        with pytest.raises(ConflictError):
            ingest_document(session, data(), "Second text.")
        session.rollback()
        assert first.status == "active" and first.version == 1


def test_uniqueness_constraints(knowledge_db):
    with Session(knowledge_db) as session:
        chunk = session.scalar(select(KnowledgeChunk))
        session.add(KnowledgeChunk(document_id=chunk.document_id, chunk_index=chunk.chunk_index,
                                   content="Duplicate index", normalized_content="duplicate index",
                                   word_count=2, content_hash=digest("Duplicate index")))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        doc = session.scalar(select(KnowledgeDocument))
        values = {c.name: getattr(doc, c.name) for c in KnowledgeDocument.__table__.columns
                  if c.name not in {"id", "version", "lock_version"}}
        session.add(KnowledgeDocument(**values, version=doc.version + 1))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_evaluation_cli_nonzero_on_failed_threshold(tmp_path, monkeypatch):
    from nourish_nest.evals import knowledge_retrieval
    monkeypatch.setitem(knowledge_retrieval.THRESHOLDS, "hit_at_1", Decimal("1.1"))
    monkeypatch.setattr(sys, "argv", ["knowledge_retrieval", "--output", str(tmp_path)])
    assert knowledge_retrieval.main() == 1


def test_ingestion_cli_offline(tmp_path, monkeypatch, knowledge_db, capsys):
    from nourish_nest import knowledge_ingestion
    (tmp_path / "note.md").write_text("CLI original fixture.")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"documents": [{**data().model_dump(mode="json"),
                                                   "path": "note.md"}]}))
    monkeypatch.setattr(sys, "argv", ["knowledge_ingestion", "--manifest", str(manifest)])
    monkeypatch.setattr(knowledge_ingestion, "SessionLocal", lambda: Session(knowledge_db))
    monkeypatch.setattr(get_settings(), "knowledge_ingestion_roots", [str(tmp_path)])
    assert knowledge_ingestion.main() == 0
    assert json.loads(capsys.readouterr().out)["documents"][0]["action"] == "created"
    (tmp_path / "note.md").write_text("api_key=do-not-log-this")
    assert knowledge_ingestion.main() == 1
    assert "do-not-log-this" not in capsys.readouterr().out
