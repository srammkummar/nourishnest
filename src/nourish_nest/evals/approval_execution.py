"""Original offline approval fault cases with independent domain-output assertions."""

import argparse
import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from time import perf_counter
from unittest.mock import patch
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from nourish_nest.approval_contracts import (
    ApprovalRequest,
    DecisionRequest,
    ExecutionRequest,
    ProposalRequest,
    payload_hash,
)
from nourish_nest.approval_critic import CriticAgent
from nourish_nest.approval_services import (
    ApprovalProposalService,
    ControlledExecutionService,
    HumanApprovalService,
)
from nourish_nest.approval_snapshot import run_fingerprint
from nourish_nest.assistant_provider import AssistantError
from nourish_nest.evals.multi_agent_fixtures import (
    HOME,
    OTHER,
    SMOKE_MESSAGE,
    domain_snapshot,
    evaluation_database,
)
from nourish_nest.models import (
    AgentActionExecution,
    AgentActionProposal,
    AgentApprovalEvent,
    AgentRun,
    AgentRunSnapshot,
    Food,
    GroceryItemRecipeSource,
    GroceryList,
    GroceryListItem,
    utc_now,
)
from nourish_nest.multi_agent_orchestrator import MultiAgentOrchestrator
from nourish_nest.multi_agent_schemas import MultiAgentRequest
from nourish_nest.services import NotFoundError

CRITIC_CASES = ["pass", "warning", "allergy", "diet", "foreign_recipe", "foreign_member", "failed_agent",
    "missing_snapshot", "run_changed", "negative", "precision", "lineage", "duplicate", "servings", "count",
    "version", "citation", "injection", "stale", "budget", "prohibited", "unsafe", "missing_grocery"]
CASES = ([{"id": f"critic-{name}", "kind": "critic", "value": name} for name in CRITIC_CASES]
    + [{"id": f"transition-{status}-{action}", "kind": "transition", "value": [status, action]}
       for status, action in [("proposed", "reject"), ("proposed", "cancel"), ("approved", "cancel"),
          ("proposed", "execute"), ("approved", "approve"), ("approved", "reject"),
          ("rejected", "approve"), ("cancelled", "approve"), ("completed", "approve"),
          ("failed", "approve"), ("expired", "approve"), ("rejected", "execute"), ("cancelled", "execute")]]
    + [{"id": f"confirmation-{i}", "kind": "confirmation", "value": value}
       for i, value in enumerate([False, "true", 1, None, "yes", 0])]
    + [{"id": f"stale-version-{value}", "kind": "stale", "value": value} for value in (2, 3, 20)]
    + [{"id": name, "kind": name} for name in ["expiry-approval", "expiry-execution", "tamper-approval",
        "tamper-execution", "tamper-rehash", "duplicate-execution", "changed-key", "changed-request",
        "cross-home-get", "cross-home-approve", "cross-home-execute", "race-execution", "race-approval",
        "rollback", "quantities", "lineage", "redaction", "repeatability", "source-changed-after-approval"]])


def invoke(engine, cls, method, *args):
    with Session(engine) as session:
        return getattr(cls(session), method)(*args)


def proposal(engine, run):
    return invoke(engine, ApprovalProposalService, "create", HOME, run, ProposalRequest(list_name="Weekly meals"), "private-request")


def approve(engine, p):
    return invoke(engine, HumanApprovalService, "decide", HOME, p.id, "approved",
        ApprovalRequest(expected_version=p.version, confirmation=True, payload_hash=p.payload_hash), "private-request")


def execution_request(p, **changes):
    return ExecutionRequest(**{"expected_version": p.version, "payload_hash": p.payload_hash,
                               "idempotency_key": "private-key", **changes})


def execute(engine, p, request=None, home=HOME):
    return invoke(engine, ControlledExecutionService, "execute", home, p.id, request or execution_request(p), "private-request")


def expect(code, call):
    try:
        call()
    except AssistantError as error:
        assert error.code == code, (error.code, code)
    else:
        raise AssertionError(f"Expected {code}")


def mutate_source(engine, run_id, name):
    with Session(engine) as session:
        run = session.get(AgentRun, run_id)
        saved = session.get(AgentRunSnapshot, run_id)
        data = copy.deepcopy(saved.snapshot_json)
        if name == "missing_snapshot":
            session.delete(saved)
            session.commit()
            return
        if name == "run_changed":
            run.warning_count += 1
            run.interpreted_intent_json = {**run.interpreted_intent_json, "groceries": False}
            session.commit()
            return
        if name in {"warning", "injection"}:
            data["warnings"] = ["prompt_injection_warning" if name == "injection" else "stale_read_warning"]
        elif name == "pass":
            data["warnings"] = []
        elif name == "allergy":
            food = session.get(Food, UUID(data["grocery"]["requirements"][0]["food_id"]))
            food.allergens[0].relationship_type = "may_contain"
        elif name == "diet":
            food = session.get(Food, UUID(data["grocery"]["requirements"][0]["food_id"]))
            food.dietary_tags.clear()
        elif name == "foreign_recipe":
            data["meals"][0]["recipe_id"] = str(UUID(int=706))
        elif name == "foreign_member":
            data["member_ids"] = [str(UUID(int=505))]
        elif name == "failed_agent":
            next(s for s in run.steps if s.agent_name == "grocery").status = "failed"
        elif name in {"negative", "precision"}:
            data["grocery"]["requirements"][0]["shortage_quantity"] = "-1" if name == "negative" else "0.0000001"
        elif name == "lineage":
            data["grocery"]["requirements"][0]["sources"] = []
        elif name == "duplicate":
            data["meals"][1] = data["meals"][0]
        elif name == "servings":
            data["meals"][0]["desired_servings"] = "3"
        elif name == "count":
            data["meals"].pop()
        elif name == "version":
            data["versions"]["grocery"] = "wrong"
        elif name == "citation":
            data["citations"][0]["excerpt_hash"] = "0" * 64
        elif name == "stale":
            run.completed_at = utc_now() - timedelta(hours=2)
        elif name == "budget":
            run.selected_agents_json = ["supervisor"] * 7
        elif name == "unsafe":
            data["member_ids"] = [str(UUID(int=504))]
        elif name == "missing_grocery":
            data["grocery"] = None
        data["run_hash"] = run_fingerprint(run)
        saved.snapshot_json, saved.snapshot_hash = data, payload_hash(data)
        session.commit()


def run_case(case):
    metrics = {"zero_unauthorized_mutation_accuracy": True}
    latency = None
    with evaluation_database() as engine:
        run = asyncio.run(MultiAgentOrchestrator(engine).preview(HOME, MultiAgentRequest(message=SMOKE_MESSAGE), "private-prompt-token"))
        assert run.status == "completed"
        kind, value = case["kind"], case.get("value")
        if kind == "critic":
            mutate_source(engine, run.run_id, value)
            before = domain_snapshot(engine)
            with Session(engine) as session:
                result = CriticAgent().review(session, HOME, run.run_id,
                    action="delete_pantry" if value == "prohibited" else "create_grocery_list")
                again = CriticAgent().review(session, HOME, run.run_id,
                    action="delete_pantry" if value == "prohibited" else "create_grocery_list")
            assert result == again
            expected = "pass" if value == "pass" else "pass_with_warnings" if value in {"warning", "injection"} else "block"
            assert result.decision == expected, result
            if value == "allergy":
                assert "allergy_conflict" in result.blocking_issues
                metrics["allergy_safety_accuracy"] = True
            if expected == "block" and value != "prohibited":
                expect("critic_blocked", lambda: proposal(engine, run.run_id))
            assert before == domain_snapshot(engine)
            metrics.update(critic_blocker_accuracy=True, deterministic_repeat_accuracy=True)
            return {"id": case["id"], "metrics": metrics, "latency_ms": None}
        before = domain_snapshot(engine)
        p = proposal(engine, run.run_id)
        assert domain_snapshot(engine) == before
        if kind == "confirmation":
            try:
                ApprovalRequest(expected_version=1, confirmation=value, payload_hash=p.payload_hash)
            except ValidationError:
                pass
            else:
                raise AssertionError("Confirmation was accepted")
            metrics["invalid_transition_accuracy"] = True
        elif kind == "stale":
            expect("stale_proposal_version", lambda: invoke(engine, HumanApprovalService, "decide", HOME, p.id, "approved",
                ApprovalRequest(expected_version=value, confirmation=True, payload_hash=p.payload_hash), "test"))
            metrics["stale_version_accuracy"] = True
        elif kind.startswith("cross-home"):
            try:
                if kind == "cross-home-get":
                    invoke(engine, ApprovalProposalService, "get", OTHER, p.id)
                elif kind == "cross-home-approve":
                    invoke(engine, HumanApprovalService, "decide", OTHER, p.id, "approved",
                        ApprovalRequest(expected_version=1, confirmation=True, payload_hash=p.payload_hash), "test")
                else:
                    execute(engine, p, home=OTHER)
            except NotFoundError:
                pass
            else:
                raise AssertionError("Cross-household access")
            metrics["household_isolation_accuracy"] = True
        elif kind == "transition":
            status, action = value
            if status == "approved":
                p = approve(engine, p)
            elif status != "proposed":
                with Session(engine) as session:
                    session.get(AgentActionProposal, p.id).status = status
                    session.commit()
            allowed = (status, action) in {("proposed", "reject"), ("proposed", "cancel"), ("approved", "cancel")}
            def call():
                if action == "execute":
                    return execute(engine, p)
                request = (ApprovalRequest(expected_version=p.version, confirmation=True, payload_hash=p.payload_hash)
                           if action == "approve" else DecisionRequest(expected_version=p.version))
                return invoke(engine, HumanApprovalService, "decide", HOME, p.id,
                              {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[action], request, "test")
            if allowed:
                assert call().status == {"reject": "rejected", "cancel": "cancelled"}[action]
            else:
                expect("invalid_approval_transition", call)
            metrics["invalid_transition_accuracy"] = True
        elif kind == "race-approval":
            gate = Barrier(2)
            def attempt():
                gate.wait()
                try:
                    return approve(engine, p).status
                except AssistantError as error:
                    return error.code
            with ThreadPoolExecutor(2) as pool:
                outcomes = list(pool.map(lambda _: attempt(), range(2)))
            assert sorted(outcomes) == ["approved", "stale_proposal_version"]
            metrics["stale_version_accuracy"] = True
        else:
            if kind not in {"expiry-approval", "tamper-approval", "tamper-rehash"}:
                p = approve(engine, p)
            assert domain_snapshot(engine) == before
            if kind.startswith("expiry"):
                with Session(engine) as session:
                    session.get(AgentActionProposal, p.id).expires_at = utc_now()-timedelta(seconds=1)
                    session.commit()
                expect("proposal_expired", lambda: approve(engine, p) if kind == "expiry-approval" else execute(engine, p))
                metrics["invalid_transition_accuracy"] = True
            elif kind.startswith("tamper"):
                with Session(engine) as session:
                    record = session.get(AgentActionProposal, p.id)
                    record.canonical_payload_json = {**record.canonical_payload_json, "shortages": []}
                    if kind == "tamper-rehash":
                        record.payload_hash = payload_hash(record.canonical_payload_json)
                        p = p.model_copy(update={"payload_hash": record.payload_hash})
                    session.commit()
                expect("proposal_hash_mismatch", lambda: execute(engine, p) if kind == "tamper-execution" else approve(engine, p))
                metrics["proposal_hash_verification_accuracy"] = True
            elif kind == "source-changed-after-approval":
                mutate_source(engine, run.run_id, "run_changed")
                expect("critic_blocked", lambda: execute(engine, p))
                metrics["proposal_hash_verification_accuracy"] = True
            elif kind == "rollback":
                from nourish_nest.grocery_generation_services import GroceryGenerationService
                original = GroceryGenerationService.persist_shortages
                def forced(self, *args):
                    original(self, *args)
                    raise RuntimeError("private-sql-secret")
                with patch.object(GroceryGenerationService, "persist_shortages", forced):
                    result = execute(engine, p)
                assert result.status == "failed" and execute(engine, p).replayed
                assert before == domain_snapshot(engine)
                metrics["rollback_accuracy"] = True
            else:
                started = perf_counter()
                if kind == "race-execution":
                    gate = Barrier(2)
                    def attempt():
                        gate.wait()
                        return execute(engine, p)
                    with ThreadPoolExecutor(2) as pool:
                        results = list(pool.map(lambda _: attempt(), range(2)))
                    assert sum(r.replayed for r in results) == 1
                    assert results[0].grocery_list_id == results[1].grocery_list_id
                    result = results[0]
                else:
                    result = execute(engine, p)
                latency = (perf_counter()-started)*1000
                assert result.status == "completed"
                if kind in {"changed-key", "changed-request"}:
                    req = execution_request(p, **({"idempotency_key": "other-key"} if kind == "changed-key" else {"expected_version": 5}))
                    expect("idempotency_conflict", lambda: execute(engine, p, req))
                else:
                    assert execute(engine, p).replayed
                with Session(engine) as session:
                    lists = list(session.scalars(select(GroceryList)))
                    items = list(session.scalars(select(GroceryListItem)))
                    sources = list(session.scalars(select(GroceryItemRecipeSource)))
                    assert len(lists) == 1 and len(items) == len(sources) == 5
                    assert all(i.required_quantity == Decimal(70) and i.required_unit == "g"
                               and i.purchased_quantity == 0 and not i.checked for i in items)
                    assert all(s.required_quantity == Decimal(100) and s.recipe_ingredient_id for s in sources)
                    assert {s.recipe_id for s in sources} == {m.recipe_id for d in run.meal_plan for m in d.meals}
                    executions = list(session.scalars(select(AgentActionExecution)))
                    assert len(executions) == 1 and executions[0].proposal_id == p.id
                    audit = [e.safe_metadata_json for e in session.scalars(select(AgentApprovalEvent))]
                    serialized = json.dumps(audit) + json.dumps(executions[0].result_summary_json)
                    assert all(word not in serialized for word in ("private-request", "private-key", "private-prompt-token", "private-sql-secret"))
                metrics.update(exactly_once_accuracy=True, grocery_output_accuracy=True, lineage_accuracy=True,
                               proposal_hash_verification_accuracy=True)
                if kind == "repeatability":
                    with Session(engine) as session:
                        from nourish_nest.approval_services import make_payload
                        saved = session.get(AgentRunSnapshot, run.run_id)
                        assert payload_hash(make_payload(saved, "Weekly   meals", "fixed", "fixed")) == payload_hash(
                            make_payload(saved, "Weekly meals", "fixed", "fixed"))
                    metrics["deterministic_repeat_accuracy"] = True
        after = domain_snapshot(engine)
        allowed = {"grocery_lists", "grocery_list_items", "grocery_generation_runs", "grocery_item_recipe_sources"}
        assert all(before[k] == after[k] for k in before if k not in allowed)
        if not metrics.get("exactly_once_accuracy"):
            assert before == after
    return {"id": case["id"], "metrics": metrics, "latency_ms": latency}


def run_evaluations(output):
    rows = []
    for case in CASES:
        try:
            rows.append({**run_case(case), "passed": True})
        except Exception as error:  # noqa: BLE001 - report every fixture failure, never user data
            rows.append({"id": case["id"], "passed": False, "failure": type(error).__name__, "metrics": {}})
    keys = sorted({k for row in rows for k in row["metrics"]})
    populations = {k: [row for row in rows if k in row["metrics"]] for k in keys}
    metrics = {k: sum(bool(r["metrics"][k]) for r in group)/len(group) for k, group in populations.items()}
    durations = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
    p95 = durations[(len(durations)*95+99)//100-1] if durations else None
    passed = all(r["passed"] for r in rows) and all(v == 1 for v in metrics.values()) and p95 is not None and p95 < 5000
    report = {"version": "approval-execution-v1", "case_count": len(rows), "passed": passed,
        "metrics": metrics, "denominators": {k: len(v) for k, v in populations.items()},
        "p95_execution_latency_ms": p95, "required_accuracy": 1, "latency_limit_ms": 5000,
        "metric_policy": "Applicable-case denominators; any failed case fails the complete evaluation.", "cases": rows}
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    (output / "report.md").write_text("# Approval execution v1\n\n" +
        f"Passed: {passed}; cases: {len(rows)}; p95: {p95} ms.\n\n" +
        "| Metric | Accuracy | Cases |\n|---|---:|---:|\n" +
        "\n".join(f"| {k} | {v} | {len(populations[k])} |" for k, v in metrics.items())+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/ai-evals/approval-execution-v1"))
    result = run_evaluations(parser.parse_args().output)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
