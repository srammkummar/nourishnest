# NourishNest architecture decisions

NourishNest is packaged as `nourish-nest`, with Python modules under
`src/nourish_nest`. FastAPI starts with `uv run uvicorn nourish_nest.api:app --reload`;
the UI starts with `uv run streamlit run streamlit_app.py`.

The Streamlit UI communicates exclusively with FastAPI through `api_client.py`.
Its wire DTOs are independent of server schemas, ORM models, repositories, and
services. The system shape below describes
the target architecture, including orchestration and integrations still to be built.

The [original architecture diagram](architecture.png), titled "AI Household Manager",
is retained as the pre-rename roadmap; it does not represent completed integrations.

## Product boundary

Phase 6B4 implements `grocery_ui.py` with standalone `grocery_client_models.py`
wire types. The shared API client handles CRUD, previews, generation, and purchase.
Per-household state retains selections and per-list pending generation/purchase
payloads. Widget state is separate from recipe selections so Streamlit cleanup
cannot discard desired servings on navigation. Submitted mutations retain versions
and idempotency keys unchanged through retries; successful purchase invalidation is
separate from subsequent summary reads. Grocery calculations and intake transactions
remain entirely in the existing backend. Session receipts expose source lineage and
warning availability without inventing history endpoints or regeneration behavior.

Phase 6B3 adds `pantry_ui.py` and standalone `pantry_client_models.py` contracts.
Household-specific session state holds API snapshots, food labels, and pending
inventory actions. Each submitted action retains its typed payload, loaded version
(where accepted by the API), and idempotency key until success or explicit reset.
Expiration/FEFO/conversion/low-stock decisions remain exclusively in the backend.
Pantry reads that can mark lots expired are not automatically retried. History is
unavailable because the API exposes no transaction-read route. The UI documents
whole-lot transfer, increase-only adjustment, discard semantics, and food-scoped
unversioned FEFO consumption rather than changing these contracts. No migrations
or backend business rules are added in this phase.

Phase 6B2 implements Recipes using `recipe_ui.py` and standalone typed wire contracts
in `recipe_client_models.py`. All persisted data comes from `APIClient`; the UI
contains no ORM/service imports or nutrition/conversion formulas. Session state holds
household-specific recipe selections, cached API responses, and drafts with stable
row IDs. Display order becomes ingredient order and unique sequential instruction
numbers at submission. Decimal values are entered and serialized as strings. Save
responses update only recipe state, clear drafts before rerun, and invalidate nutrition.
System recipe edit/delete controls are absent. Migration `20260909_0009` adds recipe
optimistic versions and normalized household creation-key records. Updates (including
child-only changes) and deletes require the loaded version. The database uniqueness
constraint on household/key arbitrates concurrent creation; recipe children and the
key record commit together. Canonical request hashes distinguish replay from conflict.
The UI retains its creation key during manual retries and submits the loaded version
for mutations. Success or explicit draft cancellation clears that state. Key records
cascade with households and retain a null recipe reference after recipe deletion, so
an old key cannot silently recreate a deleted recipe. Grocery-lineage deletion errors
retain their structured code and request ID. No grocery/pantry behavior is changed.

Phase 6A adds a native Streamlit navigation shell, session household selection,
household creation, and six dashboard metrics. `GET /v1/households` supplies the
selector using the existing repository/service pattern. Other pages explain the
remaining Phase 6B workflows. The former direct nutrition form is replaced by this boundary.

Phase 6B1 implements Household member CRUD and saved-member nutrition in
`member_ui.py`. Pure wire models validate member inputs before HTTP submission;
the UI has no nutrition formulas or server imports. PUT sends the complete saved
profile, including preferences and allergies. DELETE requires a confirmation
checkbox and accepts the existing 204 response. Mutation responses update member
cards locally without querying unrelated dashboard endpoints. Member choices are
rebuilt from the current household's collection on rerun, with household-specific
widget keys. Nutrition POST results are transient and display only for the current
selection. API errors retain the shared request-ID presentation.

Phase 6B1.1 removes unscoped individual-member routes and requires household/member
IDs throughout the HTTP client, API, service, and repository lookup. Collection
routes and standalone nutrition remain unchanged. Migration `20260909_0008` adds
the non-null integer `HouseholdMember.version` (default 1) and SQLAlchemy's
`version_id_col` supplies UPDATE/DELETE predicates. Updates require `expected_version`
in the body; deletes require it in the query. The parent version explicitly advances
for every profile replacement, including dependent-collection-only edits. Explicit
version mismatches and ORM `StaleDataError` map to HTTP 409 `stale_member_version`;
failures roll back parent and child changes together. Cross-household lookups return
the existing structured 404. The UI retains edit snapshots until refresh or success,
and binds deletion confirmation to the current version. Authentication remains future
work. The adult-only nutrition restriction and deterministic calculator are unchanged.

The injectable httpx client centralizes validation, sanitized transport errors,
structured API error envelopes/request IDs, 2-second connection and 8-second read
timeouts, and one retry for safe GET requests. Mutations are never retried.
Legacy pantry summary/expiring GETs update expiration state and are explicitly
excluded from retries. Dashboard counts are separate point-in-time reads, not an
atomic snapshot; collection APIs currently have no pagination. No UI database
access or migration is introduced. Native labeled widgets and a high-contrast
green theme avoid unsafe HTML. Configuration reads only the API URL; the UI does
not import server settings or display raw transport errors/secrets.

Household discovery lists all households in the current trusted deployment.
Authentication and user-based household authorization remain future work and are
required before public deployment.

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

## Grocery purchase and pantry intake (Phase 5F)

`POST /v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}/purchase`
accepts a positive Decimal `purchased_quantity` increment, `purchased_unit`, required
`expected_item_version`, nonblank `idempotency_key` (maximum 200 characters), and
`add_to_pantry`. Intake requires an owned `pantry_location_id` and an item food
reference. Optional fields are `expiration_date`, `purchase_price`, and
`allow_overpurchase` (default false). Price is the total for this purchase event,
recorded in the household's currency; it is not a per-unit price or a payment.

Migration `20260909_0007` adds normalized `grocery_purchase_events`: existing pantry
transactions cannot audit purchases without a pantry lot and do not store request
hashes or prices. The unique household/list/item/idempotency-key constraint protects
all purchases, including those without intake. Events store original and converted
increments, resulting totals/checked status, item/list versions, price, currency,
flags, dates, and optional stock/transaction links. The purchase service only inserts
events. Household/list/item deletion cascades audit history under existing ownership
conventions; deleting pantry records nulls links while preserving the purchase event.

One transaction updates item/list versions and completion status, inserts the event,
and optionally creates a new pantry lot plus an append-only `RESTOCK` transaction.
Purchases of different items serialize through the list row (PostgreSQL lock and
optimistic version predicate), with one retry for a competing parent update. All
failures roll back together. No existing pantry lot is consumed, reserved, or merged.
An expired intake date produces an expired lot. Completion is set only when all
items are checked at purchase time; general CRUD can still edit list status/items.

The request hash includes the expected item version and all request options,
normalizing Decimal spelling and unit whitespace/case/known aliases. Replay is
checked before version/intake validation and returns the saved audit result without
creating stock, even after the original stock is deleted. A changed request conflicts.
Database uniqueness and optimistic-write failures are resolved after rollback, not
solely through a pre-query. Snapshot versions/totals returned on replay may precede
the current item state. Optional deleted stock/location references return null.

Unsupported units, cross-dimension conversion, and quantities that cannot fit
`NUMERIC(18,6)` exactly are rejected. No density is inferred or rounding applied.
Explicit overpurchase keeps the original required quantity and marks the item checked
when the total meets or exceeds it. Response schemas support those overpurchased
items and six-decimal pantry lots; the older CRUD write validations remain unchanged.
Downgrading 0007 removes purchase audit/idempotency history but does not undo stock or
grocery purchases. Refunds, reversals, and external shopping/payment flows are deferred.

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

## Phase 8A assistant boundary

The new assistant is a single coordinator in `assistant_services.py`; it does not
use the legacy task graph, agents, or RAG. Data flow is bounded HTTP request → household
and member ownership checks → guardrails → one async `ChatProvider.interpret` call →
strict intent validation → server-bound deterministic tools → typed read-only response.
No household records, member profiles, pantry data, tool credentials, recipe IDs, or
tool arguments are provided to the interpreter. Conversation input is bounded and
untrusted. Provider-generated prose, arbitrary fields/tool names/arguments, duplicate
JSON keys, oversized output, and interpretations outside the declared local grammar
are rejected. The server generates response prose and never exposes provider exception text.

Tools are explicitly limited to `recommendations`, `nutrition_summary`,
`member_nutrition`, and `grocery_shortage`, in that order where requested. Their
arguments come exclusively from scoped request IDs and validated server results.
The coordinator permits one interpretation and at most four top-level tool invocations,
with an async provider deadline and no retry/iteration loop. Existing recommendation
internals may invoke shortage calculations per candidate; the cap counts coordinator
tools, not SQL queries or those unchanged internal calculations.

`RecommendationService` remains the ranking and saved-profile safety authority;
additional requested diets use its existing `dietary_check`. Recipe nutrition is
server-provided and only scaled/summed by the wrapper. Adult target calculation uses
`HouseholdService.calculate_member_nutrition`; combined shortages use
`GroceryShortageService.preview` directly. Decimal arithmetic uses precision 28 and
half-even context, matching the core. No service HTTP calls or mutations occur.
Reads run under `session.no_autoflush` so pending ORM state is not inadvertently saved.
There is no commit, flush, lock, generation, reservation, or purchase capability.

`Settings` adds `APP_AI_PROVIDER` (disabled/fake), reserved model/key settings, timeout,
and a bounded tool budget. The key is a repr-hidden `SecretStr`. This repository lacks
an installed/configured chat SDK pattern, so no real adapter or paid call was added.
The fake grammar and validation are intentionally limited; a future real adapter
requires evaluated language-policy expansion, not simply changing the provider name.
Error codes distinguish unavailable (503), timeout (504), rate limit (429), malformed
output (502), preview-only/tool-budget/unsafe requests (422), and existing scoped 404s.

The isolated `evals` package seeds data outside the application database and runs
49 versioned cases through the actual FastAPI endpoint. SQL statement capture plus
before/after snapshots of every table test write-free behavior; independent core
service calls verify recommendation data, nutrition, and combined shortages. Golden
intent/tool expectations and stored metadata verify language extraction, clarification,
allergen/dietary safety, and grounding. JSON/Markdown reports expose metric denominators
and failed IDs/reasons. Focused tests additionally corrupt responses to ensure evaluators
detect numerical/schema/write violations and verify no-autoflush with pending ORM state.
Run `uv run python -m nourish_nest.evals.meal_planning`; no external model calls occur.

Limitations: local fake grammar only; one meal slot per requested day; no guaranteed
complete diet, calorie optimization, or allergy safety beyond available structured
metadata; 50 ranked candidate cap; pantry snapshots rather than reservations; no UI
or assistant persistence. Medical treatment and guaranteed outcomes are unsupported.
Any future write tool must introduce explicit confirmation. RAG, embeddings, and
multi-agent delegation are deferred to avoid expanding this auditable boundary.

## Phase 8B assistant presentation

`assistant_ui.py` uses the existing HTTP client exclusively. Independent wire models
in `assistant_client_models.py` avoid importing the Phase 8A backend schema graph into
Streamlit. The only page data operations are household-scoped member reads and
`APIClient.assistant_preview`, which always submits `dry_run=true` to the existing
endpoint. No deterministic calculations, guardrails, migrations, or backend routes changed.

The central HTTP request method allows a narrow read-only POST retry exception for
the assistant-preview path: two total attempts on transport or 502/503/504 failures,
with the same request ID and existing timeouts. Mutation retry policy is unchanged.
The UI performs no automatic preview calls on reruns. Successful identical submissions
reuse the latest result; recoverable errors preserve input and previous results.

Durable session dictionaries are keyed by household/member; widget values are copied
into these dictionaries to survive Streamlit widget cleanup during navigation. Context
is limited to six messages of at most 1,000 characters each, and only sent through an
explicit continuation option. Complete new requests and example buttons start fresh
context, preventing old constraints from silently affecting a new plan. State is never
persisted to the database. Clear resets the currently selected conversation.

Visible results use names, text, and vertically stacked cards; no backend JSON is
displayed. UUIDs are removed from friendly messages and confined with request IDs,
trace data, and exact versions to collapsed Technical details. All warnings remain
available in their result sections and the main safety section. The page has no write
controls. Fake mode is labelled a local deterministic demo, with no paid/live model calls.

## Phase 9A presentation system

`ui_design.py` owns the page-header registry, grouped navigation mapping, safe HTML labels, local image registry, status badges, profile initials, workflow steps and seven-day cards. `static/assets/theme.css` centralizes tokens, responsive breakpoints (1100 and 768 pixels), keyboard focus and reduced-motion rules. Existing Streamlit widgets provide forms, selectors and confirmation controls. `dashboard_ui.py` composes household-local snapshots and existing HTTP responses; no new server contract is introduced.

All application data still arrives through the typed HTTP API client. UI modules do not import ORM models, repositories, database sessions or backend services. Asset loading reads only packaged presentation files: small original SVGs and CSS are cached, and WebP photos use local Streamlit static URLs, including the configured base URL path. User-provided names are escaped and IDs are filtered from custom markup. Source and license records live in `static/assets/ATTRIBUTION.md`.

The existing pantry read endpoints can persist expiration status. Pantry/dashboard snapshots therefore require an explicit Refresh action (or refresh following an authorized stock mutation); ordinary initial page rendering does not invoke those endpoints. This is a presentation boundary, not a change to expiration rules. Recommendation, nutrition and recipe card reads reuse existing read-only HTTP operations. Nutrition card results are cached by recipe/version and invalidated on recipe refresh/save; pantry matches require an explicit request.

Recipe create/edit/delete versions and retained idempotency keys, pantry FEFO and inventory actions, grocery generation/purchase transactions, adult nutrition restrictions, and assistant safety/provider behavior remain owned by their existing layers. The configured assistant provider is informational in collapsed Technical details; the UI never invokes a model directly.
