# Phase 10A: offline knowledge foundation

Approved documents provide traceable context for future agents. This phase retrieves
evidence only: it does not generate answers/meal plans, call an LLM, download models,
connect to a vector service, or integrate retrieval into the existing assistant.
The legacy `rag.py` interface is preserved.

USDA structured API records, pantry quantities, calories, recipe scaling, shortages,
and inventory operations remain deterministic application data rather than RAG evidence.
RAG is appropriate for reviewed explanatory documentation, approved reference prose,
and household notes administrators are permitted to ingest. Do not ingest credentials,
private keys, system prompts, conversation transcripts, generated answers, or arbitrary
repository files. Original examples and evaluation notes are not authoritative health guidance.

## Schema and ownership

Migration `20260913_0010` adds `knowledge_documents` and `knowledge_chunks`; persistence
requires it. Migrations 0001–0009 remain unchanged. UUIDs identify evidence; randomness
never selects or scores evidence. Source/visibility/status use checked portable enums.
Global documents require NULL household IDs; household documents require existing IDs.
Household deletion cascades its documents and chunks; global records remain independent.
Chunk indexes are unique per document. Checks enforce positive versions/counts,
nonempty labels/content and hash lengths. Indexes cover household/status/source,
document chunks, revision identity and active scoped hashes.

The repository filters active status, visibility, and source types **before** computing
corpus statistics. A household sees globals plus its own private documents. Retrieval
uses `session.no_autoflush`, with no flush, commit, lock, or application-data mutation.

## Revision and deduplication policy

SHA-256 content hashes cover text after whitespace is collapsed within each line and
leading/trailing whitespace is stripped; line breaks remain. A second hash covers
scope plus content hash. Active-content uniqueness is scoped, so deduplication cannot
reveal private evidence from another household. Identical active content returns the
first document and its citation metadata as `unchanged`. Aliases and metadata-only
changes are not persisted. Global/private copies remain separate.

Lineage is SHA-256 of JSON `[scope, source_name, source_uri or title]`. Keep source
identity stable for revisions. Changed content creates the next positive `version`
and marks the prior active revision `superseded`; prior chunks remain for audit.
Reverting to superseded content creates another revision. Changing source identity
creates another lineage. Revising a source to content already active under another
source is rejected for administrator review, rather than silently changing lineage.

Unique partial indexes on SQLite/PostgreSQL enforce one active revision per lineage
and one active hash per scope. A separate `lock_version` provides optimistic updates;
PostgreSQL also locks existing active revisions. Concurrent first inserts can lose
a uniqueness race and roll back; administrators can retry the manifest. There is no
chunk mutation service. Privileged direct SQL can bypass service policy.

Manifest ingestion is atomic, including supersession. Failure leaves no partial records
and does not retain rejected raw documents. `failed` status is reserved for quarantine.

## Chunking: structure-v1

1. Collapse whitespace within lines, preserving punctuation, case and blank paragraphs.
2. Recognize Markdown ATX headings (`#` through `######`) and maintain their hierarchy.
3. Flush on heading changes; greedily pack whole paragraphs up to the target word count.
4. Split oversized paragraphs into target-sized word windows, advancing by
   `target - overlap`, stopping at the final word. Never split a word.
5. Store title/heading separately, sequential chunk index, word count, content hash and
   chunking version. Chunks join paragraph words with spaces.

Defaults: target 180 words, overlap 30. Overlap applies only within oversized paragraphs,
not across headings or whole-paragraph boundaries; short paragraphs are not duplicated
to fill an overlap budget. Headings without body content are rejected. Titles are indexed
as metadata rather than repeatedly inserted into citation excerpts. Excerpts quote the
stored chunk exactly, not the original file's line layout.

Limitations: no PDF/HTML, tables/code-fence interpretation, setext headings, language
tokenizer, semantic segmentation or cross-paragraph overlap. Markdown is plain untrusted
evidence. Changed chunking algorithms require deliberate reindexing/versioning; identical
active documents are not automatically rechunked.

## Retrieval and reranking: knowledge-retrieval-v1

Unicode NFKC, case folding, whitespace normalization, Unicode word tokens and a small
explicit English stop-word set normalize queries and text. Title + heading + content
form each searchable chunk. BM25 uses Decimal precision 28:

```text
N = visible, active, source-filtered chunk count
df(t) = chunks containing term t; tf(t,c) = term frequency
L = searchable token count; avgL = mean visible token count
idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
BM25(c,q) = sum_t idf(t) * tf(t,c) * (k1 + 1)
                       / (tf(t,c) + k1 * (1 - b + b * L/avgL))
k1 = 1.5; b = 0.75; query terms are unique
```

Only chunks matching at least one non-stop query term become candidates. Zero overlap,
punctuation-only and stopword-only queries return no evidence. Candidate count defaults
to 20 (1–100), top-k to 5 (1–20); candidate count must be at least top-k.

The separate reranker uses named constants and exposes each raw signal:

```text
R = 1*BM25 + 2*overlap + 1*title + 1*heading + 0.5*phrase + 0.1*source
overlap = matched unique query terms / unique query terms
title, heading = query-term intersection fraction for the field
phrase = normalized query token sequence occurs in chunk content (0 or 1)
source = documentation/food-safety: 1; nutrition: 0.8; recipe: 0.5; user: 0
```

Source priority is a small policy signal, not proof of authority. JSON scores are Decimal
strings. Return original BM25, final rerank score, all raw components, per-term BM25
contributions, matched terms and final rank. Ties use normalized title, source name,
URI, document hash, scope, revision, chunk index, then persisted chunk ID.

`Chunker`, `Retriever` and `Reranker` protocols allow replacements without API changes.
Future PostgreSQL full-text/vector retrieval must retain scope filtering, citations and
evaluation boundaries. No optional `rag` dependency is required now.

## API and citation contract

`POST /v1/households/{household_id}/knowledge/retrieve` is read-only. Queries are trimmed,
nonblank and bounded to 1,000 characters. Unknown fields/types or unsafe limits return
existing `invalid_request` errors (422); missing households return `not_found` (404).
Existing request-ID middleware and database error contracts apply. No ingestion route
or UI was added; existing APIs are unchanged.

Each result is a structured citation containing document/chunk IDs, document title,
source name, optional URI, heading path, chunk index, exact stored excerpt, scores,
components, matched terms and rank. Empty results include `no_relevant_evidence`.
Potential injection produces `untrusted_instructions` with its chunk ID, preserving
the evidence. Conflicting documents may both appear; retrieval does not resolve truth.

Example citation shape (illustrative IDs/scores; production values come from stored rows):

```json
{
  "rank": 1,
  "document_id": "00000000-0000-0000-0000-000000000001",
  "chunk_id": "00000000-0000-0000-0000-000000000002",
  "document_title": "Example note", "source_name": "Original fixture",
  "source_uri": null, "heading_path": "Labels", "chunk_index": 0,
  "excerpt": "Label the container.", "retrieval_score": "0.5", "reranking_score": "2.5",
  "matched_terms": ["container"],
  "score_components": {"lexical": "0.5", "overlap": "1", "title": "0", "heading": "0",
                       "phrase": "0", "source": "0", "term:container": "0.5"}
}
```

## Security boundary and practical limits

Ingest only explicitly listed UTF-8 `.txt`/`.md` files, at most 1 MiB each, from a manifest
of at most 100 entries and 1 MiB. Roots default to empty (deny all). Resolved file paths
must remain under configured roots, rejecting traversal/symlink escapes, URLs and UNC
shares. Source URIs are inert attribution strings, never fetched. Administrators must
use genuinely local roots (not mapped network drives) and keep files/roots under trusted
control during ingestion; hostile concurrent filesystem replacement is not addressed.

The CLI validates declared ownership and household existence, not a user's authentication.
As in the existing local application, household URL IDs are not authentication. An
authenticated principal-to-household authorization layer and PostgreSQL RLS remain
deployment requirements; this local API is not ready for exposure to untrusted tenants.

Credential signatures are rejected as defense in depth; admins must still review for
secrets, prompts and generated answers. Injection detection is a phrase heuristic, not
a complete sanitizer. Regardless of flags, documents never execute instructions or
alter permissions. Modules do not log full private text/queries. CLI errors omit exception
payloads and excerpts. Retrieval performs no external calls.

Lexical matching is not semantic understanding. Synonyms without term overlap can miss;
weak shared words can produce false positives. In-memory corpus scanning scales poorly;
scores change as the visible corpus changes. Scores are not confidence or medical authority.

## Evaluation methodology

The versioned dataset has 36 original cases covering direct and shared-word paraphrases,
headings, filters, global/private visibility, no-answer, injection, conflicts, duplicate
content and deterministic ties. It runs the real pipeline in isolated in-memory SQLite;
separate database-backed API tests validate transport contracts and no writes.

Hit@1 ≥ .85, Hit@3/5 ≥ .95 and MRR ≥ .90 allow modest lexical ranking limitations.
Citation correctness, isolation, no-answer, deterministic repeat and source-filter accuracy
must equal 1.0 on these curated fixtures. Extra conflict/tie, duplicate and injection checks
must all pass. The command exits nonzero on failure. Reports include cases, denominators
and thresholds. Citation correctness is per response (all citations exact); empty responses
pass citation/isolation checks vacuously, with no-answer scored separately. These small
regression fixtures do not establish broad real-world quality.

## Exact commands (PowerShell, repository root)

Offline mode prevents network access by uv; dependencies must already be cached.

```powershell
$env:UV_OFFLINE = '1'
uv sync --extra dev
uv run alembic upgrade head
$env:APP_KNOWLEDGE_INGESTION_ROOTS = ConvertTo-Json -Compress -InputObject @((Resolve-Path examples/knowledge).Path)
uv run python -m nourish_nest.knowledge_ingestion --manifest examples/knowledge/manifest.json
uv run python -m uvicorn nourish_nest.api:app --host 127.0.0.1 --port 8000
```

From another terminal, use an existing household UUID (local loopback request):

```powershell
$householdId = '<existing-household-uuid>'
$body = @{ query = 'container labels'; top_k = 5; candidate_count = 20;
           source_types = @('nourishnest_documentation') } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/households/$householdId/knowledge/retrieve" -ContentType 'application/json' -Body $body
```

Use a temporary database for destructive migration round trips. Downgrading 0010 removes
knowledge history, not just an index. The build directory must exist first.

```powershell
$env:UV_OFFLINE = '1'
$env:APP_DATABASE_URL = 'sqlite:///./build/phase10a-verification.db'
uv sync --extra dev
uv run alembic upgrade head
uv run python -m pytest tests/test_knowledge.py tests/test_recipe_integrity.py
uv run python -m nourish_nest.evals.knowledge_retrieval
uv run python -m pytest
uv run ruff check .
git diff --check
uv run alembic downgrade -1
uv run alembic upgrade head
uv run alembic current
```

Reports: `artifacts/ai-evals/knowledge-retrieval-v1/report.json` and `report.md`.
Configuration: `APP_KNOWLEDGE_CHUNK_TARGET` and `APP_KNOWLEDGE_CHUNK_OVERLAP`
(overlap must be below target), plus JSON-list `APP_KNOWLEDGE_INGESTION_ROOTS`.
No Ollama installation, embedding download, cloud/paid AI call or external network access.
