import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from threading import Barrier, get_ident
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session

from alembic import command
from nourish_nest.api import app
from nourish_nest.assistant_provider import AssistantError
from nourish_nest.config import get_settings
from nourish_nest.database import get_db
from nourish_nest.evals.multi_agent_fixtures import (
    ADULT,
    FOREIGN_MEMBER,
    HOME,
    SMOKE_MESSAGE,
    domain_snapshot,
    evaluation_database,
)
from nourish_nest.evals.multi_agent_meal_planning import DATASET, run_case
from nourish_nest.models import AgentRun, AgentStep, Household, KnowledgeChunk
from nourish_nest.multi_agent_contracts import RunState, ScopeInput, Status
from nourish_nest.multi_agent_orchestrator import MultiAgentOrchestrator
from nourish_nest.multi_agent_provider import RuleBasedMultiAgentProvider
from nourish_nest.multi_agent_schemas import MultiAgentRequest
from nourish_nest.multi_agent_tools import TOOL_SPECS, ToolRegistry, WorkflowError
from nourish_nest.multi_agent_trace import TraceRecorder

CASES = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]


@pytest.fixture
def engine():
    with evaluation_database() as engine:
        yield engine


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_workflow_golden(engine, case, caplog):
    result = run_case(engine, case)
    assert result["passed"], result
    assert "shielded future" not in caplog.text
    assert "PRIVATE_FAILURE_CANARY" not in caplog.text


def state():
    return RunState(request_id="test", household_id=HOME, member_ids=[], original_request="test",
                    tool_call_limit=12)


def test_typed_state_transitions():
    value = state()
    for status in (Status.INTERPRETING, Status.PLANNING, Status.RUNNING, Status.VALIDATING, Status.COMPLETED):
        value.transition(status)
    assert value.transitions[-1] == Status.COMPLETED
    with pytest.raises(ValueError):
        value.transition(Status.RUNNING)
    assert "original_request" not in value.model_dump()


@pytest.mark.parametrize("source,target", [(Status.RECEIVED, Status.COMPLETED),
                                          (Status.INTERPRETING, Status.VALIDATING),
                                          (Status.FAILED, Status.PLANNING)])
def test_invalid_state_transitions(source, target):
    value = state()
    value.status = source
    with pytest.raises(ValueError):
        value.transition(target)


@pytest.mark.parametrize("agent,tool,args,code", [
    ("recipe", "read_pantry_summary", None, "unauthorized_tool"),
    ("recipe", "invented", None, "unauthorized_tool"),
    ("supervisor", "validate_scope", {"household_id": "not-a-uuid"}, "tool_validation_error"),
    ("supervisor", "validate_scope", {"household_id": str(uuid4())}, "household_isolation_failed"),
])
def test_tool_registry_rejections(engine, agent, tool, args, code):
    value = state()
    recorder = TraceRecorder()
    registry = ToolRegistry(engine, value, recorder, 2)
    async def check():
        with pytest.raises(WorkflowError) as error:
            await registry.call(agent, tool, args or ScopeInput(household_id=HOME))
        assert error.value.code == code
        assert not registry.workers
    asyncio.run(check())
    assert recorder.records[0].status == "failed"
    assert value.tool_call_count == 0


def test_write_classification_rejected(engine):
    registry = ToolRegistry(engine, state(), TraceRecorder(), 2)
    registry.specs["validate_scope"] = replace(registry.specs["validate_scope"], classification="write")
    async def check():
        with pytest.raises(WorkflowError, match="unauthorized_tool"):
            await registry.call("supervisor", "validate_scope", ScopeInput(household_id=HOME))
    asyncio.run(check())


def test_readonly_connection_blocks_domain_write(engine):
    registry = ToolRegistry(engine, state(), TraceRecorder(), 2)
    before = domain_snapshot(engine)
    def forbidden(session, data):
        session.execute(text("UPDATE recipes SET name='forbidden'"))
    registry.specs["validate_scope"] = replace(registry.specs["validate_scope"], handler=forbidden)
    async def check():
        with pytest.raises(WorkflowError, match="orchestration_failed"):
            await registry.call("supervisor", "validate_scope", ScopeInput(household_id=HOME))
    asyncio.run(check())
    assert before == domain_snapshot(engine)


def test_parallel_workers_and_dependency_order(engine):
    import nourish_nest.multi_agent_tools as tools_module
    barrier = Barrier(3)
    thread_ids = set()
    names = {"read_pantry_summary", "recommend_recipes", "retrieve_knowledge"}
    def wrap(handler):
        def synchronized(session, data):
            thread_ids.add(get_ident())
            barrier.wait(timeout=5)
            return handler(session, data)
        return synchronized
    specs = tuple(replace(s, handler=wrap(s.handler)) if s.name in names else s for s in TOOL_SPECS)
    with patch.object(tools_module, "TOOL_SPECS", specs):
        response = asyncio.run(MultiAgentOrchestrator(engine).preview(
            HOME, MultiAgentRequest(message=SMOKE_MESSAGE), "parallel"))
    assert response.status == "completed" and len(thread_ids) == 3
    with Session(engine) as session:
        rows = list(session.scalars(select(AgentStep).where(AgentStep.run_id == response.run_id,
                                                          AgentStep.tool_name.is_(None))))
        first_end = max(r.completed_at for r in rows if r.agent_name in {"pantry", "recipe", "knowledge"})
        assert all(r.started_at >= first_end for r in rows if r.agent_name in {"nutrition", "grocery"})


def test_cancellation_drains_workers_and_records_failure(engine, caplog):
    async def check():
        registry = ToolRegistry(engine, state(), TraceRecorder(), 0.000001)
        with pytest.raises(WorkflowError, match="agent_timeout"):
            await registry.call("supervisor", "validate_scope", ScopeInput(household_id=HOME))
        assert not registry.workers
    asyncio.run(check())
    assert "shielded future" not in caplog.text


def test_private_trace_redaction_and_citations(engine):
    canary = "private-document-canary"
    with Session(engine) as session:
        chunk = session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.content.contains("Ambermarker")))
        chunk.content += " " + canary
        session.commit()
    response = asyncio.run(MultiAgentOrchestrator(engine).preview(HOME,
        MultiAgentRequest(message="Plan 1 dinner for 2 people", knowledge_question="ambermarker"),
        "sk-secret-header-canary"))
    assert canary in response.knowledge.citations[0].excerpt
    with Session(engine) as session:
        run = session.get(AgentRun, response.run_id)
        audit = json.dumps({"request_id": run.request_id, "intent": run.interpreted_intent_json,
                           "steps": [{"in": s.input_summary_json, "out": s.output_summary_json} for s in run.steps]})
        assert canary not in audit and "sk-secret-header-canary" not in audit
        assert "ambermarker" not in audit and "Plan 1 dinner" not in audit
        assert "chain_of_thought" not in response.model_dump_json()


def test_trace_rollback(engine):
    def reject(mapper, connection, target):
        raise RuntimeError("trace failure private text")
    event.listen(AgentStep, "before_insert", reject)
    try:
        with pytest.raises(AssistantError, match="Execution trace could not be saved"):
            asyncio.run(MultiAgentOrchestrator(engine).preview(HOME,
                MultiAgentRequest(message="Plan dinner"), "trace-rollback"))
    finally:
        event.remove(AgentStep, "before_insert", reject)
    with Session(engine) as session:
        assert not list(session.scalars(select(AgentRun)))
        assert not list(session.scalars(select(AgentStep)))


def test_trace_cascade(engine):
    response = asyncio.run(MultiAgentOrchestrator(engine).preview(HOME,
        MultiAgentRequest(message="Plan dinner"), "cascade"))
    with Session(engine) as session:
        session.delete(session.get(Household, HOME))
        session.commit()
        assert session.get(AgentRun, response.run_id) is None
        assert not list(session.scalars(select(AgentStep)))


def test_one_interpretation_and_no_model_factory(engine):
    provider = RuleBasedMultiAgentProvider()
    with patch.object(provider, "interpret", wraps=provider.interpret) as interpretation, \
         patch("nourish_nest.assistant_provider.get_chat_provider", side_effect=AssertionError("Model factory forbidden")):
        result = asyncio.run(MultiAgentOrchestrator(engine, provider=provider).preview(HOME,
            MultiAgentRequest(message="Plan 1 dinner for 2 people"), "one-interpretation"))
    assert result.status == "completed" and interpretation.await_count == 1


def test_hard_allergen_exclusions_including_may_contain(engine):
    response = asyncio.run(MultiAgentOrchestrator(engine).preview(HOME,
        MultiAgentRequest(message="Plan 6 vegetarian dinners for 2 people, avoid peanuts"), "allergy"))
    identifiers = {m.recipe_id for d in response.meal_plan for m in d.meals}
    assert identifiers == {UUID(int=700+i) for i in range(6)}
    assert not identifiers & {UUID(int=706), UUID(int=707), UUID(int=708), UUID(int=709)}


def test_adult_targets_use_existing_service(engine):
    response = asyncio.run(MultiAgentOrchestrator(engine).preview(HOME,
        MultiAgentRequest(message="Plan 1 dinner for 2 people", member_ids=[ADULT]), "target"))
    target = response.nutrition_summary.targets[0]
    assert target.daily_calorie_differences == [Decimal(100) - target.target.target_calories]


@pytest.fixture
def client(engine):
    def db():
        with Session(engine) as session:
            yield session
    old = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)


def test_api_smoke_and_no_domain_mutations(client, engine):
    before = domain_snapshot(engine)
    writes = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        result = client.post(f"/v1/households/{HOME}/assistant/multi-agent-meal-plan-preview",
            json={"message": SMOKE_MESSAGE}, headers={"x-request-id": "multi-agent-smoke"})
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["request_id"] == result.headers["x-request-id"] == "multi-agent-smoke"
    assert data["status"] == "completed" and len(data["meal_plan"]) == 5
    assert data["knowledge"]["citations"] and data["trace_summary"]["tool_calls"] == 9
    assert before == domain_snapshot(engine)
    assert writes and all("agent_runs" in sql or "agent_steps" in sql for sql in writes)


@pytest.mark.parametrize("payload", [{"message": " "}, {"message": "x"*2001},
    {"message": "Plan dinner", "include_knowledge": "true"},
    {"message": "Plan dinner", "tool": "delete"}, {"message": "Plan dinner", "member_ids": ["bad"]}])
def test_api_validation(client, payload):
    result = client.post(f"/v1/households/{HOME}/assistant/multi-agent-meal-plan-preview",
                         json=payload, headers={"x-request-id": "invalid-input"})
    assert result.status_code == 422 and result.json()["code"] == "invalid_request"
    assert result.json()["request_id"] == "invalid-input"


def test_api_isolation_and_existing_endpoint(client):
    result = client.post(f"/v1/households/{HOME}/assistant/multi-agent-meal-plan-preview",
        json={"message": "Plan 1 dinner for 2 people", "member_ids": [str(FOREIGN_MEMBER)]})
    assert result.status_code == 404 and result.json()["request_id"]
    assert "/v1/households/{household_id}/assistant/meal-plan-preview" in app.openapi()["paths"]
    result = client.post(f"/v1/households/{HOME}/knowledge/retrieve", json={"query": "food storage"})
    assert result.status_code == 200 and result.json()["results"]


def test_0011_migration_roundtrip(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260913_0010")
    command.upgrade(config, "head")
    engine = create_engine(url)
    assert {"agent_runs", "agent_steps"} <= set(inspect(engine).get_table_names())
    command.downgrade(config, "-1")
    assert "agent_runs" not in inspect(engine).get_table_names()
    assert "knowledge_documents" in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260913_0011"
    engine.dispose()


def test_evaluation_command(tmp_path):
    result = subprocess.run([sys.executable, "-m", "nourish_nest.evals.multi_agent_meal_planning",
                             "--output", str(tmp_path)], capture_output=True, text=True, check=False, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "shielded future" not in result.stderr
    assert json.loads((tmp_path / "report.json").read_text())["case_count"] >= 50


def test_missing_nutrition_is_not_invented(engine):
    from nourish_nest.models import Food
    with Session(engine) as session:
        food = session.get(Food, UUID(int=600))
        food.calories_per_serving = None
        session.commit()
    response = asyncio.run(MultiAgentOrchestrator(engine).preview(HOME,
        MultiAgentRequest(message="Plan 1 vegetarian dinner for 2 people"), "missing-nutrition"))
    assert response.status == "completed"
    assert response.nutrition_summary.summary.weekly.calories is None
    assert response.nutrition_summary.summary.weekly.warnings


def test_dated_low_stock_excludes_expired_lots(engine):
    from datetime import timedelta

    from nourish_nest.models import PantryItem, utc_now
    from nourish_nest.multi_agent_contracts import AgentInput, Intent
    from nourish_nest.multi_agent_tools import pantry
    with Session(engine) as session:
        lot = session.get(PantryItem, UUID(int=800))
        lot.quantity = Decimal(100)
        lot.expiration_date = utc_now().date() - timedelta(days=1)
        session.commit()
        result = pantry(session, AgentInput(household_id=HOME, intent=Intent()))
        assert UUID(int=600) in result.low_stock_food_ids
        assert UUID(int=800) not in {lot.item_id for lot in result.available}


def test_optional_isolation_failure_stops_workflow(engine):
    from nourish_nest.multi_agent_agents import SPECIALISTS
    from nourish_nest.services import NotFoundError
    async def fail(data, tools):
        raise NotFoundError("private text")
    with patch.object(SPECIALISTS["knowledge"], "execute", fail):
        response = asyncio.run(MultiAgentOrchestrator(engine).preview(HOME,
            MultiAgentRequest(message="Plan 1 dinner for 2 people"), "isolation"))
    assert response.status == "failed" and response.failure_code == "household_isolation_failed"
    assert not response.meal_plan


def test_repeated_knowledge_call_is_rejected(engine):
    from nourish_nest.multi_agent_contracts import AgentInput, Intent
    registry = ToolRegistry(engine, state(), TraceRecorder(), 2)
    async def check():
        data = AgentInput(household_id=HOME, intent=Intent())
        await registry.call("knowledge", "retrieve_knowledge", data)
        with pytest.raises(WorkflowError, match="tool_budget_exceeded"):
            await registry.call("knowledge", "retrieve_knowledge", data)
    asyncio.run(check())
    assert registry.state.tool_call_count == 1
