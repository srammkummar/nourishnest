# Phase 10B implementation report

## Baseline and migration

Initial working tree: clean. Baseline commit:
`5c77290816a8105bdd549d4af35fb8bc208aa4e5`.
Phase 10A files and migration `20260913_0010` were present. The configured database
initially reported `20260909_0009`; the existing 0010 migration was applied and
`20260913_0010 (head)` confirmed before Phase 10B implementation.

New migration `20260913_0011_multi_agent_execution_trace.py` adds `agent_runs` and
`agent_steps`. Durable persistence is justified for audits that survive responses/restarts.
It records bounded normalized intent, safe IDs/counts, agent/tool names, timing, status
and warning/failure codes. It excludes prompts, knowledge questions/bodies, secrets,
arbitrary exception messages and chain-of-thought. Stored request IDs are hashed;
public request IDs remain unchanged. Trace writes are atomic and separate from read-only
domain tools. Household deletion cascades runs; run deletion cascades steps.

Migrations 0001–0010 are unchanged. Migration 0011 was applied to the configured local
database, and a separate temporary database passed upgrade, downgrade to 0010, and
re-upgrade to `20260913_0011 (head)`. No downgrade was performed on the normal database.

## Agents and tool permissions

| Agent | Responsibility | Allowed tools |
|---|---|---|
| Supervisor | Scope, interpretation validation, execution plan, unique selection, final validation | validate_scope; validate_selection |
| Pantry | Availability, expiring inventory, dated low stock | read_pantry_summary; read_expiring_inventory |
| Recipe | Ranked candidates and hard structural constraints | recommend_recipes |
| Knowledge | One Phase 10A evidence retrieval, citations and injection warnings | retrieve_knowledge |
| Nutrition | Existing deterministic totals and adult comparisons | calculate_recipe_nutrition OR get_member_nutrition_target |
| Grocery | Combined requirement and shortage previews | preview_recipe_requirements; preview_grocery_shortages |

Each specialist has typed input/output, responsibility, allowed tools, status,
warnings/failure code and trace metadata. Agents coordinate; deterministic services
own calculations. Maximum six agent executions (including supervisor), 12 top-level
tools, one interpretation and one knowledge reranking pass. Typical full previews use
six agents and nine tools. No recursive delegation, retries or arbitrary tool arguments.

State transitions:
`received → interpreting → planning → agents_running → validating → completed`.
Clarification/refusal may terminate interpretation; insufficient eligible recipes clarify
after the first stage. Failures terminate active states and suppress the meal plan.
Pantry, Recipe and optional Knowledge agents run concurrently on separate read-only
connections/sessions. Nutrition and optional Grocery wait for recipe selection. Results
merge in deterministic logical order. Cancellation drains workers and sanitizes failures.

## Example request and abbreviated response

```json
{
  "message": "Create five vegetarian dinners for two adults, prioritize food expiring this week, stay under 35 minutes, avoid peanuts, and show the grocery shortages.",
  "member_ids": [],
  "include_knowledge": true
}
```

Synthetic fixture outcome observed during development: `completed`, five unique meals,
servings `2`, vegetarian filter, peanut hard exclusion (including `may_contain`), total
preparation/cooking limit 34 minutes, expiring-stock prioritization, nutrition totals,
grocery requirements/shortages and approved citations. No real household data is used
in examples or evaluations. Nutrition is informational, not medical advice or a complete diet.

Abbreviated recorded response (other fields omitted):

```json
{
  "status": "completed",
  "interpretation": {
    "number_of_meals": 5, "servings": "2", "diets": ["vegetarian"],
    "allergens": ["peanut"], "maximum_minutes": 34, "prioritize_expiring": true
  },
  "nutrition_summary": {
    "summary": {"weekly": {"calories": "1000.0", "protein_g": "50.0",
                           "carbohydrate_g": "100.0", "fat_g": "15.0"}}
  },
  "trace_summary": {"agents_executed": 6, "tool_calls": 9, "partial": false},
  "provider_mode": "fake-rule-based", "preview_only": true
}
```

The full artifact includes five meal slots, five shortage rows and two citations.

Typical trace:

```text
Supervisor: validate_scope
  parallel: Pantry(read_pantry_summary → read_expiring_inventory)
            Recipe(recommend_recipes)
            Knowledge(retrieve_knowledge)
  join and select unique recipes
  parallel: Nutrition(calculate_recipe_nutrition)
            Grocery(preview_recipe_requirements → preview_grocery_shortages)
Supervisor: validate_selection
Audit writer: persist sanitized run/steps
```

Missing nutrition is withheld as `null` with a warning in Phase 10B, even when the
unchanged legacy calculator returns zero-filled values with warnings. RAG supplies
explanatory excerpts only, never quantities, nutrition, allergens or purchase decisions.

## Verification

- Offline `uv sync --extra dev`: passed with existing cached dependencies.
- Migration upgrade and isolated downgrade/re-upgrade: passed; 0011 head confirmed.
- Final focused run: **202 passed**, 10 deprecation warnings, 113.57 seconds.
- Final full suite: **787 passed**, 65 deprecation warnings, 259.09 seconds; run once.
- `uv run ruff check .`: passed.
- `git diff --check`: passed (only Git's LF/CRLF conversion notices).
- Phase 10A retrieval evaluation: **36 cases passed**, all nine measured metrics 1.00.
- Multi-agent evaluation: **54 cases passed**, all 17 accuracy/contract metrics 1.00.
  p95 completed-run latency: **139.4156 ms**, below the 5,000 ms threshold.
- Recorded isolated API smoke: passed; six agents, nine tools, five meals and RAG citations.

Final [evaluation JSON](../artifacts/ai-evals/multi-agent-meal-planning-v1/report.json)
and [readable report](../artifacts/ai-evals/multi-agent-meal-planning-v1/report.md) contain
all case outcomes, metrics, thresholds and applicable-case denominators.

The [recorded API smoke](../artifacts/ai-evals/multi-agent-meal-planning-v1/smoke.json)
contains the complete synthetic request/response, visible tool trace, unchanged table list,
and SQL-write table capture. Before and after domain snapshot SHA-256 values are identical:

```text
6b6916781dd410c22627d4bdef27ccc5be75efb94ddc71fa6ebe32a660141b70
```

The only written tables were `agent_runs` and `agent_steps`. Pantry, grocery, recipe,
food, member and knowledge data remained unchanged. The schema has no persisted meal-plan
table; this workflow returns transient previews only. All evaluation/smoke databases and
the temporary migration verification database were removed after checks.

Tests include true three-worker parallelism using a barrier, dependency timestamp order,
tool authorization/budgets, cancellation without private exception logging, hard allergy
exclusions, nutrition/shortages, exact citations, scope isolation, trace rollback/cascades,
API request IDs and compatibility. Whole-domain snapshots and SQL statement capture
verify that only `agent_runs` and `agent_steps` receive writes. A deliberately misclassified
write handler is also blocked by SQLite read-only mode.

## Evaluation and limits

The versioned suite contains 54 original cases. Metrics cover intent, agent/tool selection,
authorization, constraints, allergy safety, citations, household isolation, clarification,
refusal, deterministic repetition, budgets, completion, unchanged domain data, nutrition,
shortages and fault handling. All applicable-case accuracy thresholds are 1.00 on these
deterministic fixtures; p95 completed-run latency must be ≤5,000 ms. These are local
regression thresholds, not broad language-understanding benchmarks. Reports contain
explicit applicable-case denominators.

Known limitations:

- Fixed grammar and lexical RAG; unsupported constraints clarify rather than guess.
- Stored dietary/allergen metadata requires label verification; no medical guarantees.
- Separate reads can be stale; no inventory reservation or atomic multi-agent snapshot.
- Cooperative timeouts can be exceeded by driver waits, cleanup and final audit persistence.
- Trace persistence occurs at finalization; abrupt process termination can leave no run record.
- Live PostgreSQL execution/cancellation, authenticated tenant access, RLS and admission control remain future work.
- At most 50 ranked candidates; unsupported custom member preferences stop the workflow.
- No UI integration, saved plans or write approvals; Phase 10C approval gates remain future work.

No real model calls, Ollama installation/invocation, paid/cloud AI, model downloads or
external network calls occurred. Tests use local mocks, in-process TestClient and
temporary databases. Nothing was committed or pushed.

## Exact changed files

Modified:

- `README.md`
- `docs/ARCHITECTURE.md`
- `src/nourish_nest/api.py`
- `src/nourish_nest/config.py`
- `src/nourish_nest/models.py`
- `src/nourish_nest/pantry_services.py`
- `src/nourish_nest/planning_services.py`
- `tests/test_knowledge.py` (pin Phase 10A's own migration test to 0010)

Added:

- `alembic/versions/20260913_0011_multi_agent_execution_trace.py`
- `artifacts/ai-evals/multi-agent-meal-planning-v1/report.json`
- `artifacts/ai-evals/multi-agent-meal-planning-v1/report.md`
- `artifacts/ai-evals/multi-agent-meal-planning-v1/smoke.json`
- `docs/MULTI_AGENT_ARCHITECTURE.md`
- `docs/PHASE10B_VERIFICATION.md`
- `src/nourish_nest/evals/multi_agent_fixtures.py`
- `src/nourish_nest/evals/multi_agent_meal_planning.py`
- `src/nourish_nest/evals/multi_agent_meal_planning_v1.json`
- `src/nourish_nest/evals/multi_agent_smoke.py`
- `src/nourish_nest/multi_agent_agents.py`
- `src/nourish_nest/multi_agent_contracts.py`
- `src/nourish_nest/multi_agent_orchestrator.py`
- `src/nourish_nest/multi_agent_provider.py`
- `src/nourish_nest/multi_agent_schemas.py`
- `src/nourish_nest/multi_agent_tools.py`
- `src/nourish_nest/multi_agent_trace.py`
- `tests/test_multi_agent.py`

See [architecture, commands and safety boundaries](MULTI_AGENT_ARCHITECTURE.md).
