import asyncio
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from nourish_nest.approval_contracts import (
    ApprovalRequest,
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
from nourish_nest.evals.approval_execution import CASES, run_case
from nourish_nest.evals.multi_agent_fixtures import HOME, SMOKE_MESSAGE, evaluation_database
from nourish_nest.multi_agent_orchestrator import MultiAgentOrchestrator
from nourish_nest.multi_agent_schemas import MultiAgentRequest


@pytest.fixture
def preview():
    with evaluation_database() as engine:
        response = asyncio.run(MultiAgentOrchestrator(engine).preview(
            HOME, MultiAgentRequest(message=SMOKE_MESSAGE), "private-request"))
        assert response.status == "completed"
        yield engine, response


def propose(engine, run_id):
    with Session(engine) as session:
        review = CriticAgent().review(session, HOME, run_id)
        assert review.decision != "block", review
        session.rollback()
        return ApprovalProposalService(session).create(HOME, run_id, ProposalRequest(list_name="Weekly meals"), "test")


def approve(engine, proposal):
    with Session(engine) as session:
        return HumanApprovalService(session).decide(HOME, proposal.id, "approved", ApprovalRequest(
            expected_version=proposal.version, confirmation=True, payload_hash=proposal.payload_hash), "test")


def execute(engine, proposal, key="stable-key"):
    with Session(engine) as session:
        return ControlledExecutionService(session).execute(HOME, proposal.id, ExecutionRequest(
            expected_version=proposal.version, payload_hash=proposal.payload_hash, idempotency_key=key), "test")


def test_round_trip(preview):
    engine, run = preview
    proposal = propose(engine, run.run_id)
    approved = approve(engine, proposal)
    result = execute(engine, approved)
    assert result.status == "completed", result
    assert result.item_count == 5
    replay = execute(engine, approved)
    assert replay.replayed and replay.grocery_list_id == result.grocery_list_id


def test_decimal_hash():
    assert payload_hash({"b": Decimal("1.000000"), "a": Decimal("-0")}) == payload_hash({"a": Decimal(0), "b": Decimal(1)})


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_approval_cases(case):
    assert all(run_case(case)["metrics"].values())


def test_migration_roundtrip(tmp_path, monkeypatch):
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    from alembic import command
    from nourish_nest.config import get_settings
    url = f"sqlite:///{tmp_path / 'approval-migration.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(url)
    assert "agent_action_proposals" in inspect(engine).get_table_names()
    assert {c["name"] for c in inspect(engine).get_unique_constraints("agent_action_executions")} == {
        "uq_execution_proposal", "uq_execution_key"}
    assert len(inspect(engine).get_check_constraints("agent_action_proposals")) == 4
    command.downgrade(config, "-1")
    assert "agent_action_proposals" not in inspect(engine).get_table_names()
    assert "agent_runs" in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260913_0012"
    engine.dispose()


def test_postgres_lock_path():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from nourish_nest.approval_services import proposal_query
    query = str(proposal_query(HOME, uuid4()).compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in query and "household_id" in query


@pytest.mark.parametrize("name", ["", " ", "x"*201])
def test_invalid_names(name):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProposalRequest(list_name=name)


def test_api_scope_errors_and_request_ids(preview):
    from fastapi.testclient import TestClient

    from nourish_nest.api import app
    from nourish_nest.database import get_db
    from nourish_nest.evals.multi_agent_fixtures import OTHER
    engine, run = preview
    def db():
        with Session(engine) as session:
            yield session
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = db
    try:
        with TestClient(app) as client:
            prefix = f"/v1/households/{HOME}/assistant"
            critic = client.get(f"{prefix}/runs/{run.run_id}/critic")
            assert critic.status_code == 200 and critic.json()["decision"] != "block"
            p = client.post(f"{prefix}/runs/{run.run_id}/proposals", json={"list_name": "Meals"}).json()
            path = f"{prefix}/proposals/{p['id']}"
            assert len(client.get(f"{prefix}/proposals").json()) == 1
            response = client.post(path+"/approve", json={"expected_version": 9, "confirmation": True,
                "payload_hash": p["payload_hash"]}, headers={"x-request-id": "approval-trace"})
            assert response.status_code == 409
            assert response.json()["code"] == "stale_proposal_version"
            assert response.json()["request_id"] == response.headers["x-request-id"] == "approval-trace"
            assert client.get(path.replace(str(HOME), str(OTHER))).status_code == 404
            assert client.post(path+"/approve", json={"expected_version": 1, "confirmation": "true",
                "payload_hash": p["payload_hash"]}).status_code == 422
            approved = client.post(path+"/approve", json={"expected_version": 1, "confirmation": True,
                "payload_hash": p["payload_hash"]}).json()
            request = {"expected_version": approved["version"], "payload_hash": p["payload_hash"], "idempotency_key": "api"}
            result = client.post(path+"/execute", json=request)
            assert result.status_code == 200 and result.json()["status"] == "completed"
            assert client.post(path+"/execute", json=request).json()["replayed"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_evaluation_command(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-m", "nourish_nest.evals.approval_execution", "--output", str(tmp_path)],
        capture_output=True, text=True, check=False, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def test_household_cascade(preview):
    from sqlalchemy import delete, func, select

    from nourish_nest.models import (
        AgentActionExecution,
        AgentActionProposal,
        AgentApprovalEvent,
        Household,
    )
    engine, run = preview
    p = approve(engine, propose(engine, run.run_id))
    execute(engine, p)
    with Session(engine) as session:
        session.execute(delete(Household).where(Household.id == HOME))
        session.commit()
        for model in (AgentActionProposal, AgentActionExecution, AgentApprovalEvent):
            assert session.scalar(select(func.count()).select_from(model)) == 0
