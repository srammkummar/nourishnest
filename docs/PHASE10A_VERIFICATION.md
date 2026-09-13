# Phase 10A implementation and verification report

The initial `git status --short` was clean. No existing work was overwritten.
Nothing has been committed or pushed. No UI or existing endpoint behavior was redesigned.

## Delivered

Required migration `20260913_0010_rag_knowledge_foundation.py` persists normalized
knowledge documents and chunks. Migrations 0001–0009 are unchanged. Global documents
have no household; private documents require an existing household. Active revision/hash
uniqueness, revision history, chunk indexes, checks, cascades and optimistic document
locking protect persistence. The CLI owns atomic ingestion transactions; retrieval is read-only.

Structure-aware chunking preserves title/heading metadata, packs whole paragraphs,
and splits only oversized paragraphs with a default 180-word target and 30-word overlap.
Hashes and ordering are deterministic. Identical active content is deduplicated per scope;
changed content creates an auditable revision and supersedes the prior active one.

Retrieval filters visibility and sources before BM25 scoring. BM25 uses `k1=1.5`, `b=.75`,
`idf=ln(1+(N-df+.5)/(df+.5))`; term contribution is
`idf*tf*(k1+1)/(tf+k1*(1-b+b*L/avgL))`. Scores use Decimal precision 28.
Reranking is `BM25 + 2*overlap + title + heading + .5*phrase + .1*source`.
The [knowledge guide](PHASE10A_KNOWLEDGE.md) defines every component and tie-break rule.

Every result includes an exact stored excerpt plus document/chunk IDs, title, source,
URI, heading, index, matched terms, original/rerank scores and rank. For example, the
original evaluation fixture returns the stored Rice storage excerpt:

> Cooked rice storage uses labelled containers in this fictional kitchen exercise. Refrigerated rice containers carry a storage date.

Its heading is `Kitchen notes / Cooked rice storage`, source is `Original evaluation
fixture`, and source type is `food_safety_guidance`. It is fictional test evidence,
not an authoritative food-safety recommendation. The guide includes the full citation shape.

## Evaluation

All 36 versioned original cases passed. Measured metrics and minimum thresholds:

| Metric | Measured | Threshold | Denominator |
|---|---:|---:|---:|
| Hit@1 | 1.00 | .85 | 29 answerable cases |
| Hit@3 | 1.00 | .95 | 29 answerable cases |
| Hit@5 | 1.00 | .95 | 29 answerable cases |
| Mean reciprocal rank | 1.00 | .90 | 29 answerable cases |
| Citation correctness | 1.00 | 1.00 | 36 responses |
| Household isolation | 1.00 | 1.00 | 36 responses |
| No-answer accuracy | 1.00 | 1.00 | 7 no-answer cases |
| Deterministic repeat | 1.00 | 1.00 | 36 repeated responses |
| Source-filter accuracy | 1.00 | 1.00 | 6 filtered cases |

Conflict/tie ordering, deduplication and injection-warning expectations also passed.
Reports contain all case outcomes and denominators:
[JSON](../artifacts/ai-evals/knowledge-retrieval-v1/report.json),
[Markdown](../artifacts/ai-evals/knowledge-retrieval-v1/report.md).
These are small regression fixtures, not proof of broad retrieval quality.

## Verification status

- `UV_OFFLINE=1` was set for all uv commands; no dependency or model downloads.
- `uv sync --extra dev`: passed using existing dependencies.
- `uv run alembic upgrade head`: passed on `build/phase10a-verification.db`.
- Focused final tests: **98 passed**, 12 dependency/configuration deprecation warnings.
- `uv run python -m nourish_nest.evals.knowledge_retrieval`: passed.
- `uv run python -m pytest`: **702 passed**, 61 deprecation warnings, 195.70 seconds.
  This final full-suite command ran exactly once.
- `uv run ruff check .`: passed.
- `git diff --check`: passed (Git emitted only its existing LF/CRLF conversion notices).
- `uv run alembic downgrade -1`: passed, 0010 → 0009 on the isolated database.
- `uv run alembic upgrade head`: passed, 0009 → 0010.
- `uv run alembic current`: confirmed **20260913_0010 (head)**.
- The temporary verification database was removed after confirming the head; the user's
  normal application database was not migrated or changed.

No paid/cloud AI calls, real model calls, Ollama installation, embedding downloads or
external network requests were made. Provider tests use mocks; API tests use in-process
TestClient. No temporary HTTP servers were needed. Production/local user data was not
used for migration verification. SQL capture and whole-database snapshots prove retrieval
does not mutate pantry, recipes, grocery lists, members, meal plans or knowledge records,
including when the ORM session has pending state.

## Known limits

- Lexical retrieval is not semantic understanding; synonyms can miss and weak overlaps can match.
- In-memory corpus scanning is intended for a small local corpus, not production-scale search.
- Injection detection is heuristic. Documents never execute or change permissions, even unflagged ones.
- Administrator review remains necessary for secrets, provenance and appropriate evidence.
- Local roots must be trusted; mapped network drives/concurrent hostile filesystem changes are unsupported.
- Scope isolation is enforced, but this phase adds no user authentication or PostgreSQL RLS.
- Duplicate aliases/metadata-only edits are not retained; first active citation metadata wins.
- Live PostgreSQL execution, vector retrieval, LLM generation and assistant integration are future work.

## Exact changed files

Modified:

- `README.md`
- `docs/ARCHITECTURE.md`
- `src/nourish_nest/api.py`
- `src/nourish_nest/config.py`
- `src/nourish_nest/models.py`
- `tests/test_recipe_integrity.py` (explicit old migration target, preserving its intent)

Added:

- `alembic/versions/20260913_0010_rag_knowledge_foundation.py`
- `artifacts/ai-evals/knowledge-retrieval-v1/report.json`
- `artifacts/ai-evals/knowledge-retrieval-v1/report.md`
- `docs/PHASE10A_KNOWLEDGE.md`
- `docs/PHASE10A_VERIFICATION.md`
- `examples/knowledge/kitchen-notes.md`
- `examples/knowledge/manifest.json`
- `src/nourish_nest/evals/knowledge_retrieval.py`
- `src/nourish_nest/evals/knowledge_retrieval_v1.json`
- `src/nourish_nest/knowledge_chunking.py`
- `src/nourish_nest/knowledge_ingestion.py`
- `src/nourish_nest/knowledge_repositories.py`
- `src/nourish_nest/knowledge_schemas.py`
- `src/nourish_nest/knowledge_services.py`
- `src/nourish_nest/knowledge_types.py`
- `src/nourish_nest/reranking.py`
- `src/nourish_nest/retrieval.py`
- `tests/test_knowledge.py`
