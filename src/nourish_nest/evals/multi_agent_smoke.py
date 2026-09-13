"""One isolated offline API smoke; writes only synthetic review artifacts."""

import json
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from nourish_nest.api import app
from nourish_nest.database import get_db
from nourish_nest.evals.multi_agent_fixtures import (
    HOME,
    SMOKE_MESSAGE,
    domain_snapshot,
    evaluation_database,
)


def run_smoke(output: Path):
    with evaluation_database() as engine:
        before = domain_snapshot(engine)
        writes = []
        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                writes.append(statement.split()[2])
        def db():
            with Session(engine) as session:
                yield session
        previous = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = db
        event.listen(engine, "before_cursor_execute", observe)
        try:
            with TestClient(app) as client:
                response = client.post(f"/v1/households/{HOME}/assistant/multi-agent-meal-plan-preview",
                    json={"message": SMOKE_MESSAGE, "include_knowledge": True},
                    headers={"x-request-id": "phase10b-offline-smoke"})
        finally:
            event.remove(engine, "before_cursor_execute", observe)
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
        after = domain_snapshot(engine)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "completed" and len(data["meal_plan"]) == 5
        assert data["interpretation"]["diets"] == ["vegetarian"]
        assert data["interpretation"]["allergens"] == ["peanut"]
        assert data["interpretation"]["prioritize_expiring"]
        assert data["pantry_summary"]["expiring"] and data["knowledge"]["citations"]
        assert data["nutrition_summary"] and data["grocery_shortages"]["shortages"]["requirements"]
        assert before == after and set(writes) <= {"agent_runs", "agent_steps"}
        report = {"passed": True, "request": {"message": SMOKE_MESSAGE, "include_knowledge": True},
                  "response": data, "domain_tables_unchanged": sorted(before),
                  "before_sha256": sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
                  "after_sha256": sha256(json.dumps(after, sort_keys=True).encode()).hexdigest(),
                  "write_tables": sorted(set(writes)), "temporary_database_removed_on_exit": True}
    output.mkdir(parents=True, exist_ok=True)
    (output / "smoke.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = run_smoke(Path("artifacts/ai-evals/multi-agent-meal-planning-v1"))
    print(json.dumps({"passed": result["passed"], "before_sha256": result["before_sha256"],
                      "after_sha256": result["after_sha256"], "write_tables": result["write_tables"]}, indent=2))
