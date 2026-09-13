"""Offline end-to-end golden evaluation with injected failures and independent data checks."""

import argparse
import asyncio
import json
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from sqlalchemy.orm import Session

from nourish_nest.config import Settings
from nourish_nest.evals.multi_agent_fixtures import (
    ADULT,
    FOREIGN_MEMBER,
    HOME,
    MINOR,
    domain_snapshot,
    evaluation_database,
)
from nourish_nest.food_services import calculate_recipe_nutrition
from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.models import AgentRun, KnowledgeChunk
from nourish_nest.multi_agent_agents import SPECIALISTS
from nourish_nest.multi_agent_orchestrator import MultiAgentOrchestrator
from nourish_nest.multi_agent_schemas import MultiAgentRequest
from nourish_nest.multi_agent_tools import TOOL_SPECS
from nourish_nest.repositories import RecipeRepository
from nourish_nest.services import NotFoundError

DATASET = Path(__file__).with_name("multi_agent_meal_planning_v1.json")
THRESHOLDS = {name: Decimal(1) for name in (
    "intent_accuracy", "agent_selection_accuracy", "tool_selection_accuracy", "tool_authorization_accuracy",
    "constraint_satisfaction_rate", "allergen_safety_accuracy", "citation_correctness",
    "household_isolation_accuracy", "clarification_accuracy", "refusal_accuracy",
    "deterministic_repeat_accuracy", "tool_budget_compliance", "completion_rate",
    "no_domain_mutations", "nutrition_accuracy", "shortage_accuracy", "fault_handling_accuracy",
)}
LATENCY_LIMIT_MS = Decimal(5000)


def applicable(metric, case):
    if metric in {"intent_accuracy", "agent_selection_accuracy", "tool_selection_accuracy",
                  "constraint_satisfaction_rate", "nutrition_accuracy", "completion_rate"}:
        return case["status"] == "completed"
    if metric == "allergen_safety_accuracy":
        return case["status"] == "completed" and bool(case.get("allergens") or case.get("member"))
    if metric == "citation_correctness":
        return case["status"] == "completed" and case.get("include_knowledge", True)
    if metric == "shortage_accuracy":
        return case["status"] == "completed" and bool(case.get("grocery"))
    if metric in {"tool_budget_compliance", "tool_authorization_accuracy"}:
        return case["status"] != "not_found"
    return True


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in {
            "run_id", "request_id", "duration_ms", "started_at", "completed_at", "calculation_as_of"}}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


async def invoke(engine, case):
    settings = Settings(_env_file=None, multi_agent_tool_limit=case.get("tool_limit", 12),
                        multi_agent_agent_limit=case.get("agent_limit", 6))
    request = MultiAgentRequest(message=case["message"], include_knowledge=case.get("include_knowledge", True),
        knowledge_question=case.get("knowledge_question"),
        member_ids=[{"adult": ADULT, "minor": MINOR, "foreign": FOREIGN_MEMBER}[case["member"]]]
        if case.get("member") else [])
    coordinator = MultiAgentOrchestrator(engine, settings)
    fault = case.get("fault")
    with ExitStack() as stack:
        if fault:
            agent = "knowledge" if fault == "knowledge_failure" else "recipe"
            async def fail(data, tools):
                if fault == "recipe_timeout":
                    await asyncio.sleep(1)
                elif fault == "unauthorized_tool":
                    return await tools.call("recipe", "read_pantry_summary", data)
                elif fault == "unknown_tool":
                    return await tools.call("recipe", "delete_everything", data)
                elif fault == "malformed_tool":
                    return await tools.call("recipe", "recommend_recipes", {"unexpected": "value"})
                raise RuntimeError("PRIVATE_FAILURE_CANARY")
            if fault == "workflow_timeout":
                async def slow(message):
                    await asyncio.sleep(2)
                settings.multi_agent_timeout_seconds = 0.2
                stack.enter_context(patch.object(coordinator.provider, "interpret", slow))
            else:
                if fault == "recipe_timeout":
                    settings.multi_agent_step_timeout_seconds = 0.2
                stack.enter_context(patch.object(SPECIALISTS[agent], "execute", fail))
        try:
            return await coordinator.preview(UUID(int=99999) if case.get("household") else HOME,
                                             request, f"eval-{case['id']}")
        except NotFoundError:
            return None


def score_case(engine, case, first, second, unchanged):
    metrics = dict.fromkeys(THRESHOLDS, True)
    status = first.status.value if first else "not_found"
    metrics["fault_handling_accuracy"] = status == case["status"]
    metrics["no_domain_mutations"] = unchanged
    metrics["clarification_accuracy"] = (status == "clarification_required") == (case["status"] == "clarification_required")
    metrics["refusal_accuracy"] = (status == "refused") == (case["status"] == "refused")
    if first is None:
        metrics["household_isolation_accuracy"] = case["status"] == "not_found"
        metrics["deterministic_repeat_accuracy"] = second is None
        return metrics
    if first.status == "failed":
        metrics["fault_handling_accuracy"] &= first.failure_code == case.get("failure_code")
        metrics["deterministic_repeat_accuracy"] = bool(second and (first.status, first.failure_code, first.meal_plan) ==
                                                       (second.status, second.failure_code, second.meal_plan))
    else:
        metrics["deterministic_repeat_accuracy"] = bool(second and stable(first.model_dump(mode="json")) == stable(second.model_dump(mode="json")))
    specs = {s.name: s for s in TOOL_SPECS}
    completed = [s for s in first.trace_summary.steps if s.tool_name and s.status == "completed"]
    metrics["tool_authorization_accuracy"] = all(s.tool_name in specs and
        specs[s.tool_name].allowed_agent == s.agent_name for s in completed)
    metrics["tool_budget_compliance"] = first.trace_summary.tool_calls <= case.get("tool_limit", 12) and first.trace_summary.agents_executed <= case.get("agent_limit", 6)
    if case["status"] != "completed":
        return metrics
    metrics["completion_rate"] = status == "completed"
    if status != "completed":
        for key in ("intent_accuracy", "constraint_satisfaction_rate", "nutrition_accuracy", "shortage_accuracy"):
            metrics[key] = False
        return metrics
    intent = first.interpretation
    metrics["intent_accuracy"] = (intent.number_of_meals == case["meals"] and str(intent.servings) == case["servings"]
        and intent.diets == case.get("diets", []) and intent.allergens == case.get("allergens", [])
        and intent.groceries == case.get("grocery", False))
    expected_agents = ["supervisor", "pantry", "recipe"]
    if case.get("include_knowledge", True):
        expected_agents.append("knowledge")
    expected_agents.append("nutrition")
    if case.get("grocery"):
        expected_agents.append("grocery")
    metrics["agent_selection_accuracy"] = first.execution_plan.selected_agents == expected_agents
    expected_tools = {"validate_scope", "validate_selection", "read_pantry_summary", "read_expiring_inventory", "recommend_recipes",
                      "get_member_nutrition_target" if case.get("member") else "calculate_recipe_nutrition"}
    if case.get("include_knowledge", True) and not case.get("partial"):
        expected_tools.add("retrieve_knowledge")
    if case.get("grocery"):
        expected_tools.update({"preview_recipe_requirements", "preview_grocery_shortages"})
    metrics["tool_selection_accuracy"] = {s.tool_name for s in completed} == expected_tools
    metrics["fault_handling_accuracy"] &= first.trace_summary.partial == case.get("partial", False)
    meals = [m for d in first.meal_plan for m in d.meals]
    metrics["constraint_satisfaction_rate"] = len(meals) == case["meals"] and len({m.recipe_id for m in meals}) == len(meals)
    with Session(engine) as session:
        total = Decimal(0)
        expected_daily = []
        for meal in meals:
            recipe = RecipeRepository(session).get_for_household(meal.recipe_id, HOME)
            if recipe is None:
                metrics["household_isolation_accuracy"] = False
                continue
            metrics["constraint_satisfaction_rate"] &= (
                (intent.maximum_minutes is None or recipe.preparation_minutes + recipe.cooking_minutes <= intent.maximum_minutes)
                and (not intent.cuisines or recipe.cuisine.casefold() in intent.cuisines)
                and all(set(intent.diets) <= {tag.tag.value for tag in i.food.dietary_tags} for i in recipe.ingredients))
            metrics["allergen_safety_accuracy"] &= not any(
                a.allergen in intent.allergens and a.relationship_type in {"contains", "may_contain"}
                for ingredient in recipe.ingredients for a in ingredient.food.allergens)
            nutrition = calculate_recipe_nutrition(recipe)
            total += nutrition.calories_per_serving * meal.desired_servings
            expected_daily.append({"calories": nutrition.calories_per_serving * meal.desired_servings,
                **{field: getattr(nutrition.macros_per_serving, field) * meal.desired_servings
                   for field in ("protein_g", "carbohydrate_g", "fat_g")}})
        metrics["nutrition_accuracy"] = first.nutrition_summary.summary.weekly.calories == total
        metrics["nutrition_accuracy"] &= all(
            getattr(actual, field) == expected[field]
            for actual, expected in zip(first.nutrition_summary.summary.daily, expected_daily, strict=True)
            for field in expected)
        metrics["nutrition_accuracy"] &= all(
            getattr(first.nutrition_summary.summary.weekly, field) == sum(row[field] for row in expected_daily)
            for field in ("protein_g", "carbohydrate_g", "fat_g"))
        if first.grocery_shortages:
            expected = GroceryShortageService(session).preview(HOME, GroceryRequirementsRequest(recipes=[
                {"recipe_id": m.recipe_id, "desired_servings": m.desired_servings} for m in meals]))
            metrics["shortage_accuracy"] = first.grocery_shortages.shortages.requirements == expected.requirements
        for citation in first.knowledge.citations:
            chunk = session.get(KnowledgeChunk, citation.chunk_id)
            metrics["citation_correctness"] &= bool(chunk and chunk.content == citation.excerpt and chunk.document_id == citation.document_id)
            metrics["household_isolation_accuracy"] &= bool(chunk and chunk.document.household_id in {None, HOME})
        if case.get("citation_title"):
            metrics["citation_correctness"] &= case["citation_title"] in {c.document_title for c in first.knowledge.citations}
        if case.get("no_evidence"):
            metrics["citation_correctness"] &= not first.knowledge.citations and "no_relevant_evidence" in {w.code for w in first.warnings}
        if case.get("warning"):
            metrics["citation_correctness"] &= case["warning"] in {w.code for w in first.warnings}
        run = session.get(AgentRun, first.run_id)
        metrics["fault_handling_accuracy"] &= run is not None and run.tool_call_count == first.trace_summary.tool_calls
        if case.get("parallel"):
            independent = [s for s in run.steps if s.tool_name is None and s.agent_name in {"pantry", "recipe", "knowledge"}]
            dependent = [s for s in run.steps if s.tool_name is None and s.agent_name in {"nutrition", "grocery"}]
            metrics["agent_selection_accuracy"] &= (
                max(s.started_at for s in independent) < min(s.completed_at for s in independent)
                and min(s.started_at for s in dependent) >= max(s.completed_at for s in independent))
    return metrics


def run_case(engine, case):
    before = domain_snapshot(engine)
    first = asyncio.run(invoke(engine, case))
    second = asyncio.run(invoke(engine, case))
    metrics = score_case(engine, case, first, second, before == domain_snapshot(engine))
    return {"id": case["id"], "passed": all(metrics.values()), "metrics": metrics,
            "applicable_metrics": [key for key in metrics if applicable(key, case)],
            "status": first.status.value if first else "not_found",
            "duration_ms": str(first.trace_summary.duration_ms) if first else "0",
            "failed_metrics": [k for k, v in metrics.items() if not v]}


def run_evaluations(output):
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    with evaluation_database() as engine:
        results = [run_case(engine, case) for case in cases]
    populations = {key: [r for r in results if key in r["applicable_metrics"]] for key in THRESHOLDS}
    metrics = {key: Decimal(sum(r["metrics"][key] for r in rows))/len(rows)
               for key, rows in populations.items()}
    durations = sorted(Decimal(r["duration_ms"]) for r in results if r["status"] == "completed")
    latency = durations[(len(durations)*95 + 99)//100-1]
    passed = all(metrics[k] >= threshold for k, threshold in THRESHOLDS.items()) and latency <= LATENCY_LIMIT_MS
    report = {"version": "multi-agent-meal-planning-v1", "case_count": len(results), "passed": passed,
              "metrics": {k: str(v) for k, v in metrics.items()}, "p95_orchestration_latency_ms": str(latency),
              "thresholds": {k: str(v) for k, v in THRESHOLDS.items()}, "latency_limit_ms": str(LATENCY_LIMIT_MS),
              "denominators": {key: len(rows) for key, rows in populations.items()},
              "metric_policy": "Each metric uses its applicable cases (reported denominators). Completion covers expected-success cases. Latency covers completed runs including optional failures.",
              "cases": results}
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    lines = ["# Multi-agent meal planning v1", "", f"Passed: {passed}; cases: {len(results)}", "",
             "| Metric | Score | Required |", "|---|---:|---:|"]
    lines += [f"| {k} | {v:.4f} | {THRESHOLDS[k]} |" for k, v in metrics.items()]
    lines += [f"| p95 latency (ms) | {latency} | <= {LATENCY_LIMIT_MS} |", "", report["metric_policy"], "",
              "Deterministic comparison excludes run/request IDs and timestamps; failed runs compare status/code and empty plans.",
              "", "| Case | Passed | Failed checks |", "|---|---|---|"]
    lines += [f"| {r['id']} | {r['passed']} | {', '.join(r['failed_metrics'])} |" for r in results]
    (output / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/ai-evals/multi-agent-meal-planning-v1"))
    args = parser.parse_args()
    result = run_evaluations(args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
