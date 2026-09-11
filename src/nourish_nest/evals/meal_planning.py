"""Run golden local cases against the actual HTTP coordinator and deterministic services."""

import argparse
import asyncio
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select

from nourish_nest.api import app
from nourish_nest.assistant_contracts import AssistantResponse
from nourish_nest.assistant_provider import (
    ChatRateLimited,
    ChatUnavailable,
    FakeChatProvider,
    get_chat_provider,
)
from nourish_nest.config import Settings
from nourish_nest.database import get_db
from nourish_nest.domain import ErrorBody
from nourish_nest.evals.fixtures import IDS, NOW, evaluation_database
from nourish_nest.food_services import calculate_recipe_nutrition
from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.models import Base
from nourish_nest.planning_contracts import RecommendationRequest
from nourish_nest.planning_services import RecommendationService
from nourish_nest.repositories import MemberRepository, RecipeRepository

VERSION = "meal-planning-eval-v1"
DATASET = Path(__file__).with_name("meal_planning_v1.json")
NUTRIENTS = ("calories", "protein_g", "carbohydrate_g", "fat_g")


class EvaluationProvider(FakeChatProvider):
    def __init__(self, mode="fake"):
        self.mode = mode
        self.calls = 0

    async def interpret(self, messages):
        self.calls += 1
        if self.mode == "timeout":
            await asyncio.sleep(1)
        if self.mode == "rate_limit":
            raise ChatRateLimited("secret must not escape")
        if self.mode == "unavailable":
            raise ChatUnavailable("secret must not escape")
        if self.mode == "malformed":
            return "<script>secret</script>"
        if self.mode == "oversized":
            return "x" * 8193
        raw = await super().interpret(messages)
        parsed = json.loads(raw)
        if self.mode == "arbitrary_tool":
            parsed["tools"] = ["delete_pantry"]
        if self.mode == "invented_id":
            parsed["recipe_id"] = str(UUID(int=999))
        if self.mode == "dropped_diet":
            parsed["diets"] = []
        if self.mode == "extra_arguments":
            parsed["arguments"] = {"household_id": str(IDS["other_household"])}
        if self.mode == "duplicate_keys":
            return '{"action":"plan","action":"clarify"}'
        return json.dumps(parsed)


def snapshot(session):
    return {table.name: sorted(repr(tuple(row)) for row in session.execute(select(table)))
            for table in Base.metadata.sorted_tables}


def evaluate_response(case, status, body, session, unchanged, statements, calls):
    """Independent golden assertions and numerical comparisons, with per-metric outcomes."""
    metrics = {}
    reasons = []

    def check(metric, condition):
        metrics[metric] = bool(condition)
        if not condition:
            reasons.append(metric)

    expected = case["expected"]
    check("refusal_guardrail_accuracy", status == expected["status"] and
          (status == 200 or body.get("code") == expected.get("code")))
    check("write_free_behavior", unchanged and not statements)
    check("iteration_bound", calls <= 1)
    check("request_id_preservation", body.get("request_id") == case["id"])
    try:
        if status != 200:
            ErrorBody.model_validate(body)
        else:
            AssistantResponse.model_validate(body)
        check("response_schema_validity", True)
    except ValidationError:
        check("response_schema_validity", False)
        return metrics, reasons
    if status != 200:
        check("expected_tool_selection", not body.get("tool_trace"))
        return metrics, reasons
    check("clarification_correctness", body["status"] == expected.get("outcome", "preview"))
    check("expected_tool_selection", [row["name"] for row in body["tool_trace"]] == expected["tools"])
    intent = body["interpreted_constraints"]
    check("intent_extraction_accuracy", all(intent.get(key) == value
          for key, value in expected.get("intent", {}).items()))
    if body["status"] != "preview":
        check("grounded_recipe_ids", not body["proposed_plan"] and not body["recommendations_used"])
        return metrics, reasons
    home = IDS[case.get("household", "household")]
    member_id = IDS[case["member"]] if case.get("member") else None
    member = MemberRepository(session).get(home, member_id) if member_id else None
    meals = [meal for day in body["proposed_plan"] for meal in day["meals"]]
    used = {row["recipe_id"]: row for row in body["recommendations_used"]}
    recipes = {key: RecipeRepository(session).get_for_household(UUID(key), home) for key in used}
    check("grounded_recipe_ids", bool(meals) and all(m["recipe_id"] in used for m in meals)
          and all(recipes.values()))
    if not all(recipes.values()):
        return metrics, reasons
    allergies = {a.allergen.casefold().strip() for a in member.allergies} if member else set()
    check("allergen_safety", all(not (a.relationship_type == "contains" and
          a.allergen.casefold().strip() in allergies) for r in recipes.values()
          for ingredient in r.ingredients for a in ingredient.food.allergens))
    diets = set(expected.get("intent", {}).get("diets", []))
    if member:
        diets.update(p.preference_type.value.replace("-", "_") for p in member.dietary_preferences)
    check("dietary_compliance", all(diets <= {t.tag.value for t in ingredient.food.dietary_tags}
          for r in recipes.values() for ingredient in r.ingredients))
    if expected.get("warning"):
        check("visible_warnings", any(expected["warning"] in w for w in body["warnings"]))
    deterministic = RecommendationService(session).recommend(home, RecommendationRequest(
        member_id=member_id, maximum_cooking_minutes=intent["maximum_cooking_minutes"],
        maximum_missing_ingredients=100, limit=50,
    ))
    reference = {str(row.recipe_id): row.model_dump(mode="json") for row in deterministic.recommendations}
    numbers_match = all(row == reference.get(key) for key, row in used.items())
    expected_week = dict.fromkeys(NUTRIENTS, Decimal(0))
    selections = defaultdict(Decimal)
    for day, actual_daily in zip(body["proposed_plan"], body["nutrition_summary"]["daily"], strict=True):
        expected_day = dict.fromkeys(NUTRIENTS, Decimal(0))
        for meal in day["meals"]:
            quantity = Decimal(meal["desired_servings"])
            selections[meal["recipe_id"]] += quantity
            nutrition = calculate_recipe_nutrition(recipes[meal["recipe_id"]])
            per_serving = {"calories": nutrition.calories_per_serving,
                           **nutrition.macros_per_serving.model_dump()}
            for field in NUTRIENTS:
                expected_day[field] += per_serving[field] * quantity
        numbers_match &= all(Decimal(actual_daily[key]) == value for key, value in expected_day.items())
        for key, value in expected_day.items():
            expected_week[key] += value
    numbers_match &= all(Decimal(body["nutrition_summary"]["weekly"][key]) == value
                         for key, value in expected_week.items())
    if body["grocery_shortage_preview"]:
        expected_shortage = GroceryShortageService(session).preview(home, GroceryRequirementsRequest(
            recipes=[{"recipe_id": key, "desired_servings": value} for key, value in selections.items()]
        )).model_dump(mode="json")
        numbers_match &= expected_shortage == body["grocery_shortage_preview"]
    check("numerical_consistency", numbers_match)
    return metrics, reasons


def run_case(case):
    engine, sessions = evaluation_database()
    provider = EvaluationProvider(case.get("provider", "fake"))
    overrides = app.dependency_overrides.copy()

    def db():
        with sessions() as session:
            yield session

    statements = []

    def record(_, __, statement, *args):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP")):
            statements.append(statement.split()[0])

    try:
        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_chat_provider] = lambda: provider
        data = {"user_message": case["message"], **case.get("request", {})}
        if case.get("member"):
            data["member_id"] = str(IDS[case["member"]])
        with sessions() as session:
            before = snapshot(session)
        with patch("nourish_nest.assistant_services.get_settings", return_value=Settings(
            _env_file=None, ai_provider="fake", ai_timeout_seconds=0.05,
            ai_max_tool_calls=case.get("tool_limit", 4),
        )), patch("nourish_nest.planning_services.utc_now", return_value=NOW), patch(
            "nourish_nest.grocery_shortage_services.utc_now", return_value=NOW
        ), TestClient(app) as client:
            event.listen(engine, "before_cursor_execute", record)
            response = client.post(
                f"/v1/households/{IDS[case.get('household', 'household')]}/assistant/meal-plan-preview",
                json=data, headers={"x-request-id": case["id"]},
            )
            event.remove(engine, "before_cursor_execute", record)
            with sessions() as session:
                metrics, reasons = evaluate_response(case, response.status_code, response.json(), session,
                                                     before == snapshot(session), statements, provider.calls)
        return {"id": case["id"], "passed": not reasons, "metrics": metrics, "reasons": reasons}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
        engine.dispose()


def run_evaluations(output_dir=None):
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    if dataset["evaluation_version"] != VERSION:
        raise ValueError("Evaluation dataset version mismatch")
    results = []
    for case in dataset["cases"]:
        try:
            results.append(run_case(case))
        except Exception:  # noqa: BLE001 - a failed case must still appear in the safe report
            # Preserve a complete failure report without leaking request/provider exception text.
            results.append({"id": case["id"], "passed": False,
                            "metrics": {"evaluation_execution": False},
                            "reasons": ["Unexpected evaluation execution failure"]})
    counts = defaultdict(lambda: {"passed": 0, "evaluated": 0})
    for result in results:
        for name, passed in result["metrics"].items():
            counts[name]["passed"] += int(passed)
            counts[name]["evaluated"] += 1
    scores = {name: {**count, "score": count["passed"] / count["evaluated"]}
              for name, count in sorted(counts.items())}
    passed = sum(result["passed"] for result in results)
    report = {"evaluation_version": VERSION, "total_cases": len(results), "passed_cases": passed,
              "pass_rate": passed/len(results), "metrics": scores,
              "failed_cases": [r for r in results if not r["passed"]], "cases": results,
              "live_model_calls": 0, "scope": "Fake-provider orchestration; not real-model quality"}
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "results.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        lines = [f"# {VERSION}", "", f"Cases: {passed}/{len(results)} passed ({report['pass_rate']:.1%}).",
                 "", "Local fake-provider orchestration only. Live/paid AI calls: 0.", "",
                 "| Metric | Passed / evaluated | Score |", "|---|---:|---:|"]
        lines.extend(f"| {name} | {score['passed']}/{score['evaluated']} | {score['score']:.1%} |"
                     for name, score in scores.items())
        lines += ["", "Failed cases: " + ("none" if not report["failed_cases"] else "")]
        lines.extend(f"- {r['id']}: {', '.join(r['reasons'])}" for r in report["failed_cases"])
        (output_dir / "summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="artifacts/ai-evals/meal-planning-v1")
    args = parser.parse_args()
    report = run_evaluations(args.output_dir)
    print(f"{VERSION}: {report['passed_cases']}/{report['total_cases']} passed. Reports: {args.output_dir}")
    raise SystemExit(0 if not report["failed_cases"] else 1)


if __name__ == "__main__":
    main()
