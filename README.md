# NourishNest

NourishNest is a production-oriented household management platform. The first working vertical slice calculates evidence-based adult calorie and macro targets and generates a structured daily nutrition plan. The architecture is ready to expand into meal planning, grocery optimization, pantry inventory, chores, RAG, and multi-agent orchestration.

## Current capabilities

- Streamlit interface for a household member profile
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

Run from the project directory (`household-ai` in this checkout) with uv installed:

```bash
uv sync --extra dev
uv run uvicorn nourish_nest.api:app --reload
```

In a second terminal, from the same directory:

```bash
uv run streamlit run streamlit_app.py
```

API documentation is available at `http://localhost:8000/docs`.
The Streamlit interface is available at `http://localhost:8501`.
The distribution is `nourish-nest`; Python imports use `nourish_nest`.
See [architecture decisions](docs/ARCHITECTURE.md) for implementation and roadmap details.

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
