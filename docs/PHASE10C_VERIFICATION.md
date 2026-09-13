# Phase 10C verification

Baseline: `2634a12d07bc3139a9b4fe5b7132437d0a4e97f0` (`Add Phase 10B bounded multi-agent orchestration`). `git status --short` was empty before editing. Migrations 0010 and 0011 existed; the configured database was at `20260913_0011 (head)`.

Implemented distinct CriticAgent, ApprovalProposalService, HumanApprovalService and ControlledExecutionService components. The sole allowed action is **create_grocery_list**, including positive-shortage items and their ingredient/recipe lineage. Agents retain the read-only tool registry. Existing API endpoints and grocery generation behavior remain available.

## Migration and design

Migration `20260913_0012_agent_action_approval.py` adds `agent_run_snapshots`, `agent_action_proposals`, `agent_approval_events` and `agent_action_executions`. Household and parent cascades, action/status constraints, proposal versions, execution uniqueness and scoped indexes are included. Quantities remain canonical Decimal strings in JSON and existing `NUMERIC(18,6)` domain columns. Migrations 0001–0011 were not edited. The configured database was upgraded to 0012; downgrade/upgrade verification used a separate disposable database.

The critic checks source-run/snapshot integrity, successful required agents, six-agent/twelve-tool budgets and read allowlists, household/member/recipe access, adult scope, stored allergy hard exclusions and dietary constraints, meal count/slot/servings and duplicate selections, representable quantities, shortage arithmetic, current ingredient lineage, calculation versions, citation integrity/scope, injection warnings, permitted action and preview age. It returns structured pass/pass-with-warnings/block results and explicitly identifies unverifiable medical, physical-inventory and identity claims.

| State | Allowed next states |
|---|---|
| proposed | approved, rejected, cancelled, expired |
| approved | executing, cancelled, expired |
| executing | completed, failed |
| completed, rejected, cancelled, failed, expired | terminal; identical execution requests may replay saved results |

Canonical hashing uses sorted dictionaries, recipe and food ordering, normalized UUIDs, whitespace-collapsed list names, fixed-point Decimal strings and UTC timestamps in compact UTF-8 JSON. SHA-256 covers the complete execution payload, including its source snapshot hash. Approval and execution verify the hash and rebuild the payload from trusted server evidence. No mutable UI item data is used for execution.

SQLite `BEGIN IMMEDIATE`, PostgreSQL-compatible `FOR UPDATE`, version-qualified updates, unique execution-per-proposal and unique household/key constraints protect execution. The request fingerprint includes the proposal, expected version, payload hash and idempotency key. Identical replay returns the saved result; changed requests conflict. A shared grocery writer and explicit noncommitting list-service boundary enable a single outer transaction. A domain savepoint rolls back list/items/lineage before a terminal safe failure is audited.

See [Human approval and execution](HUMAN_APPROVAL_AND_EXECUTION.md) for the five architecture diagrams, full rules and limitations.

## API and UI

All routes are scoped under `/v1/households/{household_id}/assistant`:

- `GET /runs/{run_id}/critic`
- `POST /runs/{run_id}/proposals`
- `GET /proposals`
- `GET /proposals/{proposal_id}`
- `POST /proposals/{proposal_id}/approve`
- `POST /proposals/{proposal_id}/reject`
- `POST /proposals/{proposal_id}/cancel`
- `POST /proposals/{proposal_id}/execute`

The AI Assistant switch opens the **AI-assisted, human-approved** workflow: request → multi-agent preview → critic → exact proposal review → explicit checkbox and Approve/Reject → separate Create grocery list. Cancellation is available before execution. Completed controls disable repeat creation/cancellation. Request fields, workflow mode, preview/proposal/result and retry key persist through reruns/navigation in the current Streamlit session. UUIDs and traces stay under collapsed Technical details. The screen explains missing approval authentication and that no purchasing or pantry reservation occurs.

Browser verification used **1366×768 and 1024×768** on loopback API/Streamlit ports **18630/18631**, with disposable synthetic data. Both layouts were visually inspected through the browser tool. The complete workflow created “AI-assisted weekly meals” with five items; Grocery Lists navigation showed that friendly list name and zero purchased items. Final completion state and disabled controls were rechecked at both widths. The viewport override was reset and the temporary tab closed. Both servers stopped and no listeners remained on either port. The smoke helper removed its temporary database; `build/phase10c-migration.db` was separately removed and its absence checked.

## Tests and evaluations

| Check | Result |
|---|---|
| Cached `uv sync --extra dev` with `UV_OFFLINE=1` | 165 resolved, 62 checked; no downloads |
| Focused approval, UI, legacy assistant and grocery tests | **117 passed**, 5 warnings, 142.07 s |
| Full suite, run **exactly once** | **864 passed, 1 failed**, 68 warnings, 418.04 s |
| Final affected UI/boundary checks after corrections | **24 passed**, 7.15 s |
| Knowledge retrieval evaluation | **36 cases passed** |
| Multi-agent evaluation | **54 cases passed** |
| Approval execution evaluation | **64 cases passed** |
| Migration 0012 upgrade and isolated downgrade/upgrade | Passed |
| Final configured migration | `20260913_0012 (head)` |
| Ruff and `git diff --check` | Passed |

The full-suite failure was the existing static UI import allowlist not listing the new `approval_ui` and `approval_contracts` modules. The fix adds both modules to the allowed **and inspected** sets, retaining the prohibition on SQLAlchemy and server imports. That exact test passed in the final 24-test run. Browser review also prompted targeted fixes for completed-state controls and widget restoration; those final UI changes passed the affected tests. **The full suite was not rerun**, honoring the one-run constraint; the recorded full-suite result is not represented as an all-green run.

The 64-case approval evaluation reported:

| Metric | Accuracy | Applicable cases |
|---|---:|---:|
| Critic blocker accuracy | 1.00 | 23 |
| Allergy safety | 1.00 | 1 |
| Proposal hash verification | 1.00 | 12 |
| Household isolation | 1.00 | 3 |
| Invalid transition | 1.00 | 21 |
| Stale version | 1.00 | 4 |
| Exactly once | 1.00 | 8 |
| Rollback | 1.00 | 1 |
| Grocery output | 1.00 | 8 |
| Lineage | 1.00 | 8 |
| Zero unauthorized mutation | 1.00 | 64 |
| Deterministic repeat | 1.00 | 24 |

**p95 execution latency: 94.53 ms**, required <5000 ms. All required correctness thresholds are 1.00; any failed case fails the command. Reports expose denominators rather than claiming every metric was evaluated on every case. These are synthetic software checks, not clinical safety certification or production load tests.

Reports: [approval evaluation](../artifacts/ai-evals/approval-execution-v1/report.json), [API smoke and complete example payload/result](../artifacts/ai-evals/approval-execution-v1/smoke.json), [multi-agent evaluation](../artifacts/ai-evals/multi-agent-meal-planning-v1/report.json).

## Example and mutation proof

The API smoke proposed **Offline smoke weekly meals**, five distinct vegetarian dinners at **2 servings** each. Each generated food requirement was **100 g**, reviewed pantry availability **30 g**, and approved shortage **70 g**. Execution created one grocery list, five generated items, one generation run and five ingredient-lineage records. All purchased quantities remained zero; all items remained unchecked. A second identical execution replayed the same list ID.

| Domain snapshot | SHA-256 |
|---|---|
| Before / after proposal / after approval | `641ce1e6ebd6344cf01913db9e230de977480d049447f85679b8fb81371ba62c` |
| After execution / after replay | `092f226c367f843021ce2dc2c14d0132baee60809e2ecf7a45677274f9e807eb` |

Only `grocery_lists`, `grocery_generation_runs`, `grocery_list_items`, and `grocery_item_recipe_sources` changed among the 22 domain tables. The other 18 stayed identical, including pantry quantities/transactions, purchase events, members, recipes, foods and knowledge. Approval/run audit writes are separately expected. Forced post-item failure rolled back every domain write; two-session races produced one committed execution and one replay.

## Known limitations

Authentication remains absent; selected household IDs are not production authorization or proof of approver identity. Hashes are integrity checks, not signatures against a privileged database attacker. Existing pre-0012 runs require fresh previews. PostgreSQL lock SQL is compilation-tested, not live-server tested. SQLite has a single-writer lock and can return a retryable concurrency conflict. Critic reads do not reserve inventory or provide global serializable isolation against unrelated domain-edit endpoints. Pantry quantities are the approved snapshot; physical freshness, complete allergen metadata and medical appropriateness remain unverifiable. Optional knowledge failures warn; required-agent failures block. Failed proposals are terminal, and reversal/reservation remain future work. Streamlit state is session-local.

No commit or push was made. No Ollama installation/invocation, model download, paid/cloud AI call or external network request occurred. Browser/API traffic was limited to the explicitly requested local loopback smoke test.

## Exact changed files

```text
README.md
alembic/versions/20260913_0012_agent_action_approval.py
artifacts/ai-evals/approval-execution-v1/report.json
artifacts/ai-evals/approval-execution-v1/report.md
artifacts/ai-evals/approval-execution-v1/smoke.json
artifacts/ai-evals/multi-agent-meal-planning-v1/report.json
artifacts/ai-evals/multi-agent-meal-planning-v1/report.md
docs/ARCHITECTURE.md
docs/HUMAN_APPROVAL_AND_EXECUTION.md
docs/MULTI_AGENT_ARCHITECTURE.md
docs/PHASE10C_VERIFICATION.md
src/nourish_nest/api.py
src/nourish_nest/api_client.py
src/nourish_nest/approval_contracts.py
src/nourish_nest/approval_critic.py
src/nourish_nest/approval_services.py
src/nourish_nest/approval_snapshot.py
src/nourish_nest/approval_ui.py
src/nourish_nest/assistant_ui.py
src/nourish_nest/evals/approval_execution.py
src/nourish_nest/evals/approval_smoke.py
src/nourish_nest/evals/multi_agent_fixtures.py
src/nourish_nest/evals/multi_agent_smoke.py
src/nourish_nest/grocery_generation_services.py
src/nourish_nest/grocery_services.py
src/nourish_nest/models.py
src/nourish_nest/multi_agent_trace.py
tests/test_approval.py
tests/test_approval_ui.py
tests/test_multi_agent.py
tests/test_ui.py
```

Ignored local verification logs are under `build/phase10c-*.log`; no temporary server or database is retained.
