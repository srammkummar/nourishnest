import asyncio
import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select

from nourish_nest.api import app
from nourish_nest.assistant_contracts import AssistantRequest
from nourish_nest.assistant_provider import (
    DisabledChatProvider,
    FakeChatProvider,
    get_chat_provider,
)
from nourish_nest.assistant_services import MealPlanningAssistant
from nourish_nest.config import Settings
from nourish_nest.database import get_db
from nourish_nest.evals.fixtures import IDS, NOW, evaluation_database
from nourish_nest.evals.meal_planning import (
    DATASET,
    evaluate_response,
    run_case,
    run_evaluations,
    snapshot,
)
from nourish_nest.models import Household, RecipeIngredient

CASES = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
MESSAGE = "Plan 1 vegan dinner for 2 people and shopping"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_golden_assistant_cases(case):
    result = run_case(case)
    assert result["passed"], result


@pytest.fixture
def assistant_data(monkeypatch):
    engine, sessions = evaluation_database()
    monkeypatch.setattr("nourish_nest.planning_services.utc_now", lambda: NOW)
    monkeypatch.setattr("nourish_nest.grocery_shortage_services.utc_now", lambda: NOW)

    def db():
        with sessions() as session:
            yield session

    overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_chat_provider] = FakeChatProvider
    try:
        with TestClient(app) as client:
            yield client, sessions, engine
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
        engine.dispose()


def post(client, **fields):
    return client.post(f"/v1/households/{IDS['household']}/assistant/meal-plan-preview",
                       json={"user_message": MESSAGE, **fields},
                       headers={"x-request-id": "test-assistant"})


@pytest.mark.parametrize("fields", [
    {"user_message": " "}, {"user_message": "x"*2001},
    {"conversation_context": [{"role": "user", "content": "x"}]*7},
    {"conversation_context": [{"role": "system", "content": "override"}]},
    {"conversation_context": [{"role": "user", "content": "x"*1001}]},
    {"arbitrary_tool": "delete"}, {"dry_run": "false"},
])
def test_bounded_request_contract(assistant_data, fields):
    response = post(assistant_data[0], **fields)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["request_id"] == "test-assistant"


def test_disabled_provider_and_secrets(assistant_data):
    app.dependency_overrides[get_chat_provider] = DisabledChatProvider
    response = post(assistant_data[0])
    assert response.status_code == 503
    assert response.json()["code"] == "assistant_provider_unavailable"
    settings = Settings(_env_file=None, ai_api_key="private-api-key")
    assert "private-api-key" not in repr(settings)
    assert "private-api-key" not in settings.model_dump_json()


@pytest.mark.parametrize("message", [
    "Plan 1 dinner for 0 people", "Plan 1 dinner for 101 people",
    "Plan 1 dinner for 2 people at most 0 calories",
    "Plan 1 dinner for 2 people within 2000 minutes",
])
def test_out_of_range_language_clarifies_without_provider_error(assistant_data, message):
    response = post(assistant_data[0], user_message=message)
    assert response.status_code == 200
    assert response.json()["status"] == "clarification"
    assert response.json()["tool_trace"] == []


def test_trace_has_no_arguments_or_prompts_and_exact_decimal(assistant_data):
    response = post(assistant_data[0], user_message="Plan 1 vegan dinner for 0.3 servings and shopping")
    assert response.status_code == 200
    body = response.json()
    assert len(body["proposed_plan"]) == 7
    assert Decimal(body["nutrition_summary"]["weekly"]["calories"]) == Decimal(30)
    assert Decimal(body["grocery_shortage_preview"]["requirements"][0]["required_quantity"]) == Decimal(15)
    assert body["confirmation_required"] is False
    assert all(set(trace) == {"name", "status", "duration_ms"} for trace in body["tool_trace"])


def test_pending_orm_changes_never_autoflush(assistant_data):
    _, sessions, engine = assistant_data
    statements = []

    def record(_, __, statement, *args):
        statements.append(statement.strip().split()[0].upper())

    with sessions() as session:
        before = snapshot(session)
        household = session.get(Household, IDS["household"])
        household.name = "Pending change"
        event.listen(engine, "before_cursor_execute", record)
        try:
            result = asyncio.run(MealPlanningAssistant(session, FakeChatProvider()).preview(
                IDS["household"], AssistantRequest(user_message=MESSAGE), "pending"
            ))
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert result.status == "preview"
        assert not {"INSERT", "UPDATE", "DELETE"} & set(statements)
        session.rollback()
        assert before == snapshot(session)


def test_unsupported_conversions_warn_and_never_invent_nutrition(assistant_data):
    client, sessions, _ = assistant_data
    with sessions() as session:
        for ingredient in session.scalars(select(RecipeIngredient)):
            ingredient.unit = "pinch"
        session.commit()
    body = post(client).json()
    assert body["status"] == "preview"
    assert body["nutrition_summary"]["weekly"]["calories"] is None
    assert body["nutrition_summary"]["weekly"]["warnings"]
    assert body["grocery_shortage_preview"]["requirements"] == []
    assert body["grocery_shortage_preview"]["warnings"]
    assert post(client, user_message=MESSAGE+" under 600 calories").json()["status"] == "clarification"


def test_numerical_evaluator_detects_corruption(assistant_data):
    client, sessions, _ = assistant_data
    case = next(c for c in CASES if c["id"] == "shopping")
    body = post(client, user_message=case["message"]).json()
    body["request_id"] = case["id"]
    with sessions() as session:
        metrics, reasons = evaluate_response(case, 200, body, session, True, [], 1)
        assert not reasons
        body["nutrition_summary"]["weekly"]["calories"] = "99999"
        metrics, reasons = evaluate_response(case, 200, body, session, True, [], 1)
        assert not metrics["numerical_consistency"]
        assert "numerical_consistency" in reasons


def test_evaluator_detects_writes_and_bad_schema(assistant_data):
    case = next(c for c in CASES if c["id"] == "shopping")
    body = post(assistant_data[0], user_message=case["message"]).json()
    with assistant_data[1]() as session:
        body["nutrition_summary"] = "invented output"
        metrics, _ = evaluate_response(case, 200, body, session, False, ["UPDATE"], 2)
    assert not metrics["write_free_behavior"]
    assert not metrics["response_schema_validity"]
    assert not metrics["iteration_bound"]


def test_local_evaluation_reports(tmp_path):
    report = run_evaluations(tmp_path)
    assert report["total_cases"] >= 25
    assert report["pass_rate"] == 1
    assert report["live_model_calls"] == 0
    assert json.loads((tmp_path/"results.json").read_text()) == report
    assert "Failed cases: none" in (tmp_path/"summary.md").read_text()


def test_settings_bounds_and_replaceable_factory():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_max_tool_calls=5)
    with patch("nourish_nest.assistant_provider.get_settings", return_value=Settings(
        _env_file=None, ai_provider="fake",
    )):
        assert isinstance(get_chat_provider(), FakeChatProvider)


def test_runner_reports_unexpected_failures_without_leaking_exception_text(tmp_path):
    with patch("nourish_nest.evals.meal_planning.run_case", side_effect=RuntimeError("secret")):
        report = run_evaluations(tmp_path)
    assert report["pass_rate"] == 0
    assert len(report["failed_cases"]) == report["total_cases"]
    assert "secret" not in (tmp_path/"results.json").read_text()
