# NourishNest architecture decisions

NourishNest is packaged as `nourish-nest`, with Python modules under
`src/nourish_nest`. FastAPI starts with `uv run uvicorn nourish_nest.api:app --reload`;
the UI starts with `uv run streamlit run streamlit_app.py`.

The current Streamlit UI calls `nourish_nest.nutrition` directly. FastAPI exposes
the same deterministic service independently. The system shape below describes
the target architecture, including orchestration and integrations still to be built.

The [original architecture diagram](architecture.png), titled "AI Household Manager",
is retained as the pre-rename roadmap; it does not represent completed integrations.

## Product boundary

The system manages household planning: nutrition targets, meal plans, grocery lists, pantry inventory, budgets, and chores. It may prepare carts and calendar events but cannot execute either without explicit confirmation.

## System shape

```text
Streamlit UI
    -> FastAPI boundary and Pydantic validation
    -> deterministic router / future LangGraph supervisor
    -> specialized agents
    -> deterministic domain services and approved adapters
    -> validated result
    -> human approval
    -> external action
```

## Decision 1: deterministic nutrition calculations

Calorie, macro, price, unit-conversion, allergen, and budget logic must be implemented as versioned functions with tests. LLM output cannot directly determine these values. The LLM may explain the values or use them while drafting a plan.

## Decision 2: multi-agent orchestration after domain services

Agent orchestration will be added only after each agent has a typed input, typed output, narrow tool permissions, timeouts, and deterministic validation. This prevents a large conversational agent from becoming the business-logic layer.

Planned agents:

- Nutrition agent
- Meal-planning agent
- Inventory agent
- Shopping agent
- Budget agent
- Chore agent
- Supervisor/coordinator

## Decision 3: RAG is for household knowledge, not arithmetic

RAG will ground answers in recipes, pantry records, receipts, product details, and household preferences. The pipeline will use metadata-aware chunks, hybrid retrieval, reranking, citations, and a frozen evaluation set.

Initial evaluation targets:

- Hit@5 >= 0.85
- MRR >= 0.75
- Citation accuracy >= 0.95
- Faithfulness >= 0.90
- Allergen constraint accuracy = 1.00
- Budget arithmetic accuracy = 1.00

## Decision 4: safety and approval

- Version 0.1 supports adult educational estimates only.
- Pregnancy, eating-disorder risk, pediatric needs, medical conditions, and therapeutic diets require professional review.
- Shopping orders, payments, messages, and calendar writes require explicit approval.
- Every external mutation will use an idempotency key and audit record.

## Decision 5: provider adapters

External dependencies sit behind interfaces. Development uses deterministic fixtures or mocks. Production adapters can later connect to USDA nutrition data, grocery catalogs, calendars, notification services, vector stores, and rerankers without changing core domain logic.

## Decision 6: food and recipe calculations

Food and recipe records use SQLAlchemy models separate from Pydantic API schemas.
Nutrition quantities are stored as precise numeric values. Deterministic conversion
uses grams as the canonical mass unit, milliliters as the canonical volume unit, and
items as the canonical count unit. Volume-to-mass conversion requires known density;
the system never guesses it. Recipe nutrition aggregates ingredient nutrition,
allergens, and the intersection of ingredient dietary tags, then scales totals by
recipe servings. Development fixtures are explicit, opt-in, and unavailable in
production.

## Decision 7: external food providers

External food sources implement the `FoodDataProvider` contract. The USDA provider
uses only the official FoodData Central API, typed response normalization, a central
HTTP client with timeouts, safe-GET retries, rate-limit handling, and a TTL cache.
Provider search and detail calls are read-only; import and refresh are explicit
database mutations. The application selects `fake` or `usda` through configuration,
and tests inject `FakeFoodDataProvider` or mocked HTTP transports. API keys are never
logged, cached, returned, or committed.

Food provenance keeps `source_type` as the broad classification and uses the nullable
`source_provider` plus `external_source_identifier` as the external identity. External
foods require both fields; USDA imports use `source_provider=usda_fdc`. This permits
different providers to use the same external identifier without collisions.

## Decision 8: pantry inventory

Pantry inventory is modeled as household-owned lots rather than one aggregate row,
so expiration dates and package history remain available. Quantities use Decimal
arithmetic and the shared deterministic unit converter. Consumption uses
first-expiring-first-out, refuses incompatible or unsupported units, prevents
negative stock, and records append-only transactions. Transfers, adjustments,
consumption, and discards commit atomically; idempotency keys and optimistic versions
protect retries and stale writes. Expiration and low-stock queries are indexed and
the expiring-soon window is configurable. SQLAlchemy emits version-qualified updates
and PostgreSQL executions use `SELECT FOR UPDATE` for consumption and item mutations.
SQLite does not provide row-level locks and instead serializes database writers; it is
appropriate for local development but PostgreSQL is the production concurrency target.
Transactions are never updated or deleted by pantry services. Household deletion
cascades lots and their audit history because audit rows are household-owned.

## Decision 9: grocery database foundation (Phase 5A)

`GroceryList` belongs to one required household; `GroceryListItem` inherits ownership
through its required list. Future access must scope lists by household and items
through that list; this schema does not implement authorization or PostgreSQL RLS.
Household deletion cascades lists, and list deletion cascades items. Optional food
references restrict food deletion. Source references are nullable UUID metadata,
without a polymorphic foreign key or source-household validation at this stage.

Quantities use Python Decimal and `NUMERIC(18, 6)` with nonnegative checks. Status
and source values have database checks; purchased quantity defaults to zero and
checked defaults to false. Composite household/status and list/checked indexes,
plus status and food indexes, support future scoped queries. Both models use
`version_id_col` for ORM update/delete conflicts; bulk SQL bypasses this protection,
and item changes do not increment the parent list version. Timestamps are maintained
by the ORM. SQLite NUMERIC affinity does not provide PostgreSQL's exact decimal
storage guarantees, so SQLite remains a development target.

Migration `20260908_0005` adds only these two tables. Grocery calculation, pantry
subtraction, services, API/UI, agents, and RAG remain future work.

## Grocery generation persistence (Phase 5E1)

Migration `20260908_0006` adds generation runs with a unique household/list/idempotency
key and recipe contribution snapshots using `NUMERIC(18, 6)`. Runs cascade with their
household or list; sources cascade with their grocery item. Deleting a run nulls the
optional item reference. Ingredient deletion nulls its optional source reference,
preserving quantity snapshots when existing recipe updates replace ingredients.
Recipe references use deferred `NO ACTION`: deletion is blocked at commit while
sources remain, but the existing household cascade can remove both in one transaction.
Database foreign-key actions do not increment item optimistic versions.

This phase adds persistence only. Future generation services must validate that run,
list, item, recipe, and ingredient references belong together and enforce recipe
access; individual foreign keys do not enforce these cross-record relationships.
Hash computation and idempotent replay behavior are not implemented. SQLite retains
its NUMERIC storage limitations; PostgreSQL remains the exact-decimal target.

## Atomic grocery generation (Phase 5E2)

`POST /v1/households/{household_id}/grocery-lists/{list_id}/generations` accepts
Phase 5C recipe selections, required `expected_list_version`, and a trimmed nonblank
`idempotency_key` of at most 200 characters. Only draft/active lists can be generated.
The service calls Phase 5D directly and saves only positive shortages. Source rows
preserve every contributing recipe's full requirement before pantry subtraction;
their sum can therefore exceed the generated item's shortage quantity.

One transaction covers the version-qualified list update, run, items, and sources.
PostgreSQL also locks the list row; pantry rows are never locked, changed, or reserved.
The database key constraint and list version update protect concurrent attempts;
failed uniqueness/version writes roll back before resolving a committed winner.
Manual items remain untouched. Even complete pantry coverage creates a run and
increments the list version. A list supports one successful generation; replacement
and regeneration are deferred.

The SHA-256 request hash canonicalizes recipe order and Decimal servings and includes
`expected_list_version`. Exact replay is checked before current status/version rules;
a changed request under the same key conflicts. Replay returns the run's currently
persisted items/lineage and the current list version without recalculating or writing.
Revision 0006 has no warning snapshot storage: first responses include inherited
warnings with `warnings_available=true`; replay returns `warnings=[]` and
`warnings_available=false`. `calculation_as_of` is retained in the run's creation time.
This is not immutable response history: subsequent item edits/deletes affect replay.

Quantities requiring more than six decimal places or exceeding `NUMERIC(18,6)` are
rejected with `invalid_request`, without rounding or partial writes. Inventory remains
a point-in-time estimate. The one-generation rule is enforced by this transactional
service; arbitrary direct SQL can bypass it. No schema changes were added.

## Next vertical slice

1. **Database Phase 1 complete:** household/member persistence with Alembic migrations,
   structured dietary preferences and allergies, and repository/service boundaries.
2. **Database Phase 2 complete:** food and recipe persistence, deterministic units,
   recipe nutrition, allergen propagation, and dietary compatibility.
3. **USDA provider complete:** FoodData Central search, detail, import, refresh,
   typed normalization, retries, rate limits, and caching.
4. **Pantry Phase 4 complete:** household inventory lots, FEFO consumption,
   expiration, low-stock rules, transfers, and audit transactions.
5. Seven-day meal planner that meets calorie/macro bounds.
6. Consolidated grocery list generated from recipe ingredients minus pantry inventory.
7. Streamlit review and approval workflow.
8. Golden evaluation dataset covering nutrition constraints, allergies, missing data, and budget conflicts.

Database migrations use `uv run alembic upgrade head` to upgrade and
`uv run alembic downgrade -1` to roll back one revision. Local development uses
SQLite; the schema uses portable SQLAlchemy UUID, timestamp, enum, and cascade definitions.
