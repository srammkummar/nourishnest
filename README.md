# NourishNest

NourishNest is a production-oriented household management platform. The first working vertical slice calculates evidence-based adult calorie and macro targets and generates a structured daily nutrition plan. The architecture is ready to expand into meal planning, grocery optimization, pantry inventory, chores, RAG, and multi-agent orchestration.

## Current capabilities

- Streamlit household dashboard, household selection/creation, and navigation shell
- HTTP-only typed API client with timeouts, safe GET retries, and request-ID errors
- Saved household member management and API-based nutrition estimates
- Recipe, pantry, and grocery workflow placeholders for later Phase 6B work
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
preference/allergy collections. Recipes, Pantry, and Grocery Lists remain placeholders.

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
