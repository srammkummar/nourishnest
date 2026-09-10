# NourishNest

NourishNest is a production-oriented household management platform. The first working vertical slice calculates evidence-based adult calorie and macro targets and generates a structured daily nutrition plan. The architecture is ready to expand into meal planning, grocery optimization, pantry inventory, chores, RAG, and multi-agent orchestration.

## Current capabilities

- Streamlit household dashboard, household selection/creation, and navigation shell
- HTTP-only typed API client with timeouts, safe GET retries, and request-ID errors
- Saved household member management and API-based nutrition estimates
- Recipe browsing, editing, and API-calculated nutrition
- Pantry and grocery workflow placeholders for later Phase 6B work
- FastAPI health and nutrition-calculation endpoints
- Deterministic Mifflin–St Jeor calorie calculation
- Goal-aware calorie adjustment with conservative safety bounds
- Deterministic protein, fat, carbohydrate, and meal allocation
- Structured warnings for low calorie targets and unsupported minors
- Request IDs and consistent API error responses
- Agent routing contracts and a deterministic coordinator
- Metadata-aware document chunking foundation for RAG
- Unit and API tests
- Docker packaging

## Architecture principle

LLMs will interpret requests, coordinate agents, retrieve context, and draft plans. Deterministic code owns calculations, dietary constraints, validation, budget totals, and external transactions. Purchases and calendar changes require explicit user approval.

## Quick start

With uv installed, run these commands in two Windows PowerShell terminals.
Terminal 1 (FastAPI; keep it running):

```powershell
Set-Location 'C:\Users\sramm\OneDrive\Documents\Mastering-Agentic-AI\UV-Project\nourishnest'
uv sync --extra dev
uv run alembic upgrade head
uv run python -m uvicorn nourish_nest.api:app --host 127.0.0.1 --port 8000 --reload
```

Terminal 2 (Streamlit; keep it running):

```powershell
Set-Location 'C:\Users\sramm\OneDrive\Documents\Mastering-Agentic-AI\UV-Project\nourishnest'
$env:APP_API_BASE_URL = 'http://127.0.0.1:8000'
uv run python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

API documentation is available at `http://localhost:8000/docs`.
The Streamlit interface is available at `http://localhost:8501`.
The distribution is `nourish-nest`; Python imports use `nourish_nest`.
See [architecture decisions](docs/ARCHITECTURE.md) for implementation and roadmap details.

`APP_API_BASE_URL` defaults to `http://127.0.0.1:8000`. Set it in the Streamlit
terminal or in `.env`; environment variables take precedence. Restart Streamlit
after changing configuration. Docker Compose uses `http://api:8000` internally.
The existing `uv run streamlit run streamlit_app.py` entrypoint remains supported.
The commands above use Python modules to avoid stale Windows console launchers
(for example, `uv trampoline failed to canonicalize script path`).

If port 8000 is unavailable, check the API terminal for startup errors and run:

```powershell
Test-NetConnection 127.0.0.1 -Port 8000
Invoke-RestMethod http://127.0.0.1:8000/health
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

If another application owns the port, use `--port 8001` in terminal 1 and
`$env:APP_API_BASE_URL = 'http://127.0.0.1:8001'` in terminal 2 before restarting
Streamlit. Do not stop unrelated processes. The health badge checks API reachability;
a dashboard error may still indicate missing migrations or unavailable storage.
Errors include a request ID for matching API logs. Household creation is never
automatically retried; after a timeout, refresh and check the selector before
submitting again.

### Phase 6A UI scope

The dashboard shows member and readable recipe counts, active pantry lots,
expiring lots (the API's configured expiration window), foods below saved stock
thresholds, and grocery lists whose status is `active`. Refresh retrieves current
values. Empty households have a friendly starting state. Quick actions open member
management and nutrition; pantry and grocery actions remain explanatory placeholders.
The selected household persists within the Streamlit session.

The selector uses the new `GET /v1/households` collection route. This application
currently assumes a trusted deployment: household selection is not authentication
or authorization. Do not expose it publicly without an access-control layer.
Counts come from separate requests and are not a single database snapshot.
Existing pantry summary/expiration GET routes can mark expired lots; the UI client
does not retry those calls. Other safe GETs retry once on transport failures or
502/503/504 responses; POSTs are sent once. No migrations change in Phase 6A.

### Phase 6B1: members and nutrition

On **Household**, use the sidebar to select a household or create another one.
The page displays its name and ID, saved member cards, and Add/Edit/Delete actions.
Forms include the existing profile fields with units, editable preference rows,
and allergy rows with severity and notes. Select a row in either table to remove it.
Deletion requires a member-specific confirmation checkbox. Successful changes
update the member cards without loading unrelated dashboard data.

On **Nutrition**, select a saved member and choose **Calculate nutrition**. The UI
calls `POST /v1/households/{household_id}/members/{member_id}/nutrition/calculate` and displays BMR, estimated
TDEE, calorie and macro targets, the returned calculation version, and API warnings.
Calculations are not persisted and results disappear on navigation or another
rerun, avoiding display of an estimate for a different member. All calculations
remain in FastAPI. Results are estimates, not medical advice.

Contract limits: member profiles accept ages 13–100, but the current nutrition
calculator rejects minors. Calculator sex options are female/male. Height must
exceed 100 cm and weight must exceed 30 kg. Loss/gain needs a positive weekly
change; maintenance saves zero. Nutrition profile numbers follow the existing
floating-point API contract. Member updates replace the complete profile and
preference/allergy collections. Pantry and Grocery Lists remain placeholders.

### Phase 6B1.1: member API integrity

Apply migration `20260909_0008` with `uv run alembic upgrade head` before starting
the updated API. Existing members receive integer `version = 1`. Migrations
0001–0007 are unchanged. Restart FastAPI and Streamlit together after upgrading.

All individual member operations now use `/v1/households/{household_id}/members/{member_id}`:

- `GET`: read a member, including `version`.
- `PUT`: replace the complete profile with required `expected_version` in the JSON body.
- `DELETE`: require `?expected_version=N`; success remains HTTP 204.
- `POST .../nutrition/calculate`: calculate from the owned member without persisting results.

Cross-household access returns the existing structured `not_found` response.
Stale writes return HTTP 409 with `stale_member_version` and the request ID.
Successful updates increment the version, including preference/allergy-only edits.
The edit form retains its loaded version; use **Refresh members** after a conflict
to load current data before resubmitting. Deletion confirmation resets on a new version.

This is an intentional pre-release compatibility break: all `/v1/members/{member_id}`
routes, including nutrition, are removed. Collection create/list routes and standalone
`POST /v1/nutrition/calculate` remain compatible. Clients must migrate to scoped paths
and supply versions for mutations. Household ownership checks do not replace user
authentication, which remains future work. Nutrition remains adult-only (18+), although
saved profiles permit ages 13–100. Downgrading 0008 removes version history; re-upgrading
resets versions to 1, so reload all clients after a downgrade/re-upgrade.

### Phase 6B2: Recipes

Open **Recipes** in the sidebar or **Browse recipes** on the dashboard. Search recipe
names and filter by available cuisines. Household and shared system recipes are
labeled separately; system recipes have no edit/delete controls. Details display
ordered ingredients and instructions, metadata, and API-provided nutrition totals,
per-serving values, allergens, dietary tags, warnings, and calculation version.

Choose **Create recipe**, search the stored food catalog, select a food, and add
ingredients. Enter positive quantities (up to three decimal places) and servings
(up to two decimal places). Use Move up/Move down/Remove for ingredients and steps;
instruction numbers are assigned in display order. **Save recipe** sends one request.
Successful creation clears the draft; edits prepopulate the complete recipe. Delete
requires a confirmation checkbox. Drafts and selections are household-specific within
the Streamlit session. **Refresh recipes** reloads the recipe data and nutrition.

All food search uses `GET /v1/foods/search?q=...`; recipe CRUD uses
`/v1/households/{household_id}/recipes[/{recipe_id}]`, and nutrition uses
`GET /v1/households/{household_id}/recipes/{recipe_id}/nutrition`. No backend
calculations, migrations, or external imports are added.

Recipe updates replace all ingredients/instructions and require their loaded
version. Creation uses a durable idempotency key; mutations are never automatically
retried. After an ambiguous creation timeout, retry with unchanged fields and the
same draft. Recipe lists are not
paginated, so name/cuisine filters operate locally on the retrieved collection.
Nutrition can be incomplete or unavailable for missing food data or unsupported
conversions; the UI displays API warnings and does not infer density. Recipes
referenced by grocery lineage may reject deletion. Existing unusual ingredient
units remain visible during editing; new ingredients use supported unit choices.
Foods must already exist in the catalog; USDA search/import remains outside this UI.

### Recipe mutation integrity

Run `uv run alembic upgrade head` before starting the updated API. Migration
`20260909_0009` adds `Recipe.version` (existing rows start at 1) and normalized
`recipe_creation_records`. Migrations 0001–0008 are unchanged.

- Recipe responses include `version`. `PUT /v1/households/{household_id}/recipes/{recipe_id}`
  requires `expected_version` in the complete recipe body. `DELETE` on that URL
  requires `?expected_version=N`. Stale writes return HTTP 409 `stale_recipe_version`.
- `POST /v1/households/{household_id}/recipes` requires a nonblank `Idempotency-Key`
  header of at most 128 characters. The unique household/key record stores a
  SHA-256 canonical request hash, resulting recipe ID, and creation timestamp.
  Validated defaults, Decimal values, object keys, and ingredient/instruction ordering
  are canonicalized. Identical replay returns the existing recipe (HTTP 201);
  changed payloads return HTTP 409 `idempotency_conflict`.
- Creation of the recipe, ingredients, instructions, and key record commits in one
  transaction. A database uniqueness conflict rolls back the losing transaction
  before resolving the winning request. Updates and deletes also roll back on failure.
- Streamlit retains its creation key through reruns, navigation, and manual retries.
  Success clears the draft; **Cancel editing** explicitly discards it and its key.
  For a stale update, cancel and refresh before editing again. Grocery-lineage
  deletion restrictions return `recipe_in_use` with the existing message/request-ID
  envelope. System recipes remain read-only.

These are intentional API contract changes: older clients must send creation keys
and mutation versions. Replay returns the recipe's current representation, including
later edits, rather than a saved response snapshot. Key records live until household
deletion; deleting a recipe nulls their recipe reference and subsequent replay returns
`idempotency_result_deleted`, preventing accidental recreation. Browser-session loss
loses the UI's draft/key; refresh and check existing recipes before creating again.
Downgrading 0009 removes creation-key history and recipe versions; re-upgrading resets
versions to 1. Reload clients after a downgrade. PostgreSQL DDL is tested offline;
concurrent integration tests run against SQLite, not a live PostgreSQL server.

### Phase 6B3: Pantry

The Pantry page uses the typed HTTP client for summary counts, inventory search and
location/status filters, storage-location creation/deletion, adding existing foods
as inventory lots, quantity increases, FEFO consumption, whole-lot transfer, discard,
and low-stock threshold creation/update. Dashboard actions open Add item, Expiring
soon, and Low stock views. Expiration alerts and low-stock results come from the API;
the UI does not calculate stock, conversions, or FEFO ordering. Quantities are entered
as decimal text. Successful actions reset their form and refresh only Pantry data.

Endpoints used under `/v1/households/{household_id}/pantry`:

- `GET summary`, `items`, `expiring`, `expired`, `low-stock`, `stock-rules`, `locations`.
- `POST locations`, `DELETE locations/{location_id}`, `POST items`.
- `POST items/{item_id}/adjust`, `items/{item_id}/discard`, `consume`, `transfer`.
- `PUT stock-rules/{food_id}`; food lookup uses `GET /v1/foods/search` and `GET /v1/foods/{food_id}`.

Existing backend limits are intentionally preserved:

- History has no read endpoint. Its section explains this limitation; transaction
  rows cannot yet be displayed without a separate backend change. No history is
  fabricated from the current inventory snapshot.
- Adjustment only increases quantity. Transfer moves an entire lot. Discard marks
  the whole lot discarded even when a smaller quantity is specified; the UI warns
  that any remainder becomes unusable and requires explicit confirmation.
- Adjust, transfer, and discard send the loaded expected version using the API's
  `version` field. FEFO consumption is food-scoped across locations and has no
  request-version field. No unsupported version field is invented by the client.
- Inventory actions retain their key and submitted payload across manual retries,
  refreshes, and navigation. The API reports `duplicate_idempotency_key` for an
  already-used key rather than replaying a successful response. Check refreshed
  inventory before **Reset action with current stock** starts a new request.
  A lost browser session also loses pending UI state. No mutation is automatically retried.
- Lot/location creation and stock-rule writes have no version/idempotency inputs.
  After an ambiguous creation timeout, check refreshed data before submitting again.
  A location with any lot records, including depleted/discarded records, is not empty.
- Legacy inventory/summary/expiration/low-stock GETs may mark expired lots and update
  versions. The client does not retry those reads automatically. Snapshot counts
  may change between requests; **Refresh pantry** reloads current data.

Use the two-terminal launch commands above. No migrations or backend rules changed;
the database remains at `20260909_0009`.

### Phase 6B4: Grocery Lists

Grocery Lists now supports list creation, status filtering, versioned name/status
updates and confirmed deletion; exact-Decimal manual items with optional catalog
food links; recipe requirement and pantry shortage previews; pantry-aware generation;
and partial/complete purchase increments with optional pantry intake. Purchases support
explicit overpurchase, expiration, and an optional total price in household currency.
The dashboard opens list creation or the active-list filter. List/item status and
purchased-item progress come from API records; the UI performs no stock/unit calculations.

All requests use the typed HTTP client. Existing endpoints used:

- `/v1/households/{household_id}/grocery-lists`: GET/POST.
- The same path plus `/{list_id}`: GET/PUT/DELETE; updates send `expected_version`
  in JSON and deletes send it as a query parameter.
- `/{list_id}/items`: GET/POST; `/{list_id}/items/{item_id}`: GET/PUT/DELETE,
  with the same version conventions.
- `POST /v1/households/{household_id}/grocery-requirements/preview` and
  `/shortage-preview` use recipe IDs and desired servings. Results show canonical
  requirements, recipe contributions, pantry-lot contributions, and structured warnings.
- `POST .../grocery-lists/{list_id}/generations` sends `expected_list_version` and
  an idempotency key. `POST .../{list_id}/items/{item_id}/purchase` sends
  `expected_item_version`, an idempotency key, and the purchase/intake options.
- Existing recipe collection, food search/get, pantry-location, pantry-summary,
  and dashboard data endpoints supply selections and refreshes.

Recipe selections/servings and selected lists survive navigation within a Streamlit
session. Once submitted, generation and purchase requests retain their entire payload
and key through retries, refreshes, and household navigation. Only success or explicit
reset clears the pending request. No mutation is automatically retried. A successful
purchase invalidates the Pantry snapshot and refreshes grocery versions, pantry
summary, and dashboard counts; a subsequent read failure does not resubmit the purchase.

Backend limitations remain visible:

- One successful generation per list; only draft/active lists are eligible. No
  regeneration/replacement is implemented. Fully covered recipes can produce an empty run.
- Shortages are point-in-time estimates, not reservations. Generation recalculates
  them. Lineage quantities describe the full recipe contribution before pantry subtraction.
- Generation replay does not recover original warnings (`warnings_available=false`).
  Receipts/lineage are retained in this UI session; no generation/purchase history-read
  endpoints exist. Losing the browser session loses its pending keys and displayed receipts.
- List/manual-item creation has no idempotency contract: check refreshed data after
  ambiguous failures. CRUD does not infer completion; the UI displays API status.
- Manual items without a food reference cannot enter pantry. Purchase intake needs
  an existing owned location. CRUD preserves purchase totals/checked state and still
  enforces its required-versus-purchased invariant; purchased-item units are read-only
  in the edit form. Use Purchase for converted increments.

Launch with the existing two-terminal commands above. No migrations or backend
business rules changed; Alembic remains at `20260909_0009`.

## Tests

```bash
uv run pytest
uv run ruff check .
```

## Database Phase 1

Local development uses SQLite at `APP_DATABASE_URL` (default:
`sqlite:///./nourish_nest.db`). The schema uses SQLAlchemy 2 models and UUID,
timestamp, enum, and cascading foreign-key definitions compatible with PostgreSQL.
Apply or roll back migrations with:

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
```

Database tests use temporary SQLite files and do not write to the development database.

## Database Phase 2

Food and recipe persistence is available through the `/v1/foods` and household
recipe endpoints. Recipe nutrition is deterministic and uses `recipe-nutrition-v1`.
Mass calculations use grams internally; volume uses milliliters; count uses items.
Known conversions include grams, kilograms, ounces, pounds, milliliters, liters,
cups, tablespoons, teaspoons, and item/count. Volume-to-mass conversion is never
guessed without density data. Unknown units return `unsupported_conversion`, while
known units with missing density produce a nutrition warning.

Development-only fixtures are opt-in and blocked when `APP_ENV=production`:

```bash
uv run python -m nourish_nest.seed_data
```

Fixture values are illustrative and are not authoritative production nutrition data.

## Pantry Phase 4

Pantry inventory is household-scoped and supports separate lots for the same food,
location management, expiration tracking, low-stock rules, Decimal quantities,
FEFO consumption, atomic transfers, optimistic item versions, and append-only
transaction history. Mass, volume, and count use the existing deterministic unit
conversion service; density-dependent conversions are rejected rather than guessed.
Expiring-soon behavior defaults to three days and is configurable with
`APP_PANTRY_EXPIRING_SOON_DAYS`.

Pantry item versions use SQLAlchemy optimistic concurrency and mutations use
database transactions. PostgreSQL uses row locks for concurrent consumption;
SQLite serializes writers at the database level and does not provide equivalent
row-level locking. Pantry transactions are append-only during normal operation.
Deleting a household cascades its pantry lots and their audit history because the
history has no meaning outside that household.

Example workflow:

```bash
uv run alembic upgrade head
curl -X POST http://localhost:8000/v1/households/{household_id}/pantry/locations \
	-H 'content-type: application/json' \
	-d '{"name":"Refrigerator","location_type":"refrigerator"}'
curl http://localhost:8000/v1/households/{household_id}/pantry/expiring
curl http://localhost:8000/v1/households/{household_id}/pantry/low-stock
```

Pantry operations never place store orders or perform external mutations.

## USDA FoodData Central

Phase 3 provides an isolated USDA FoodData Central provider. Obtain an API key
from the [official USDA FoodData Central API documentation](https://fdc.nal.usda.gov/api-guide.html), then configure it in
your local `.env` file. Never commit the key:

```bash
APP_FOOD_DATA_PROVIDER=usda
APP_USDA_API_KEY=your-local-key
APP_USDA_BASE_URL=https://api.nal.usda.gov/fdc/v1
APP_USDA_TIMEOUT_SECONDS=10
APP_USDA_MAX_RETRIES=2
APP_USDA_CACHE_TTL_SECONDS=300
```

Search and detail requests do not persist foods. Import and refresh requests do.
Imported records retain USDA FDC ID, data type, attribution, and retrieval time,
with `source_provider=usda_fdc`; refreshing preserves the NourishNest food UUID.
Manual foods may leave `source_provider` and external identifiers empty. Local development and tests use
the fake provider; automated tests never call USDA. USDA credentials are sent only
to the provider request and are not included in logs or API responses.

## Docker

```bash
docker compose up --build
```

- API: `http://localhost:8000`
- Streamlit: `http://localhost:8501`

## Delivery roadmap

1. **Foundation — started:** nutrition domain, API, Streamlit, error contracts, tests.
2. **Persistence:** household profiles, pantry, food catalog, meal plans, feedback, migrations.
3. **Planning:** recipe library, deterministic allergen filters, meal-plan generator, shopping-list deduplication.
4. **RAG:** ingestion, chunking, hybrid retrieval, reranking, citations, golden dataset.
5. **Agents:** LangGraph supervisor with nutrition, meal, inventory, shopping, budget, and chore agents.
6. **Integrations:** USDA nutrition, grocery catalog, calendar, notifications, human approvals.
7. **Production hardening:** authentication, secrets, rate limits, retries, audit logs, observability, security review, deployment.

## Important boundary

Version 0.1 supports adults only and is intended for planning and education, not diagnosis or treatment. Pregnancy, eating-disorder risk, medical conditions, and therapeutic diets require qualified professional guidance.
