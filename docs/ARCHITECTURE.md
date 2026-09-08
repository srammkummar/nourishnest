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

## Next vertical slice

1. **Database Phase 1 complete:** household/member persistence with Alembic migrations,
   structured dietary preferences and allergies, and repository/service boundaries.
2. **Database Phase 2 complete:** food and recipe persistence, deterministic units,
   recipe nutrition, allergen propagation, and dietary compatibility.
3. Seven-day meal planner that meets calorie/macro bounds.
4. Consolidated grocery list generated from recipe ingredients minus pantry inventory.
5. Streamlit review and approval workflow.
6. Golden evaluation dataset covering nutrition constraints, allergies, missing data, and budget conflicts.

Database migrations use `uv run alembic upgrade head` to upgrade and
`uv run alembic downgrade -1` to roll back one revision. Local development uses
SQLite; the schema uses portable SQLAlchemy UUID, timestamp, enum, and cascade definitions.
