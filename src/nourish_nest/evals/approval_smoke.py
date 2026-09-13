"""Isolated offline API proof; optional disposable loopback UI server for manual review."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nourish_nest.api import app
from nourish_nest.approval_contracts import payload_hash
from nourish_nest.database import get_db
from nourish_nest.evals.multi_agent_fixtures import (
    HOME,
    SMOKE_MESSAGE,
    domain_snapshot,
    evaluation_database,
)


def run_smoke(engine, output):
    def db():
        with Session(engine) as session:
            yield session
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = db
    before = domain_snapshot(engine)
    try:
        with TestClient(app) as client:
            prefix = f"/v1/households/{HOME}/assistant"
            def post(path, data):
                response = client.post(prefix+path, json=data, headers={"x-request-id": "offline-approval-smoke"})
                assert response.status_code == 200, response.text
                return response.json()
            preview = post("/multi-agent-meal-plan-preview", {"message": SMOKE_MESSAGE})
            critic = client.get(prefix+f"/runs/{preview['run_id']}/critic").json()
            assert critic["decision"] != "block"
            p = post(f"/runs/{preview['run_id']}/proposals", {"list_name": "Offline smoke weekly meals"})
            after_proposal = domain_snapshot(engine)
            assert after_proposal == before
            approved = post(f"/proposals/{p['id']}/approve", {"expected_version": p["version"],
                "confirmation": True, "payload_hash": p["payload_hash"]})
            after_approval = domain_snapshot(engine)
            assert after_approval == before
            request = {"expected_version": approved["version"], "payload_hash": p["payload_hash"], "idempotency_key": "offline-smoke"}
            result = post(f"/proposals/{p['id']}/execute", request)
            assert result["status"] == "completed" and result["item_count"] == 5
            after_execution = domain_snapshot(engine)
            replay = post(f"/proposals/{p['id']}/execute", request)
            assert replay["replayed"] and replay["grocery_list_id"] == result["grocery_list_id"]
            assert domain_snapshot(engine) == after_execution
            changed = [k for k in before if before[k] != after_execution[k]]
            assert set(changed) == {"grocery_lists", "grocery_list_items", "grocery_generation_runs", "grocery_item_recipe_sources"}
            assert len(after_execution["grocery_lists"]) == 1
            assert len(after_execution["grocery_list_items"]) == 5
            report = {"passed": True, "proposal": p, "execution": result, "replay": replay,
                "domain_before_hash": payload_hash(before), "after_proposal_hash": payload_hash(after_proposal),
                "after_approval_hash": payload_hash(after_approval), "after_execution_hash": payload_hash(after_execution),
                "after_replay_hash": payload_hash(domain_snapshot(engine)), "changed_domain_tables": changed,
                "unchanged_domain_tables": [k for k in before if k not in changed], "temporary_database": True}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    output.mkdir(parents=True, exist_ok=True)
    (output / "smoke.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    output = Path("artifacts/ai-evals/approval-execution-v1")
    with evaluation_database() as engine:
        result = run_smoke(engine, output)
        print(json.dumps({k: v for k, v in result.items() if k not in {"proposal", "execution", "replay"}}, indent=2), flush=True)
        if args.serve:
            env = {**os.environ, "APP_DATABASE_URL": str(engine.url), "APP_AI_PROVIDER": "fake",
                   "APP_API_BASE_URL": "http://127.0.0.1:18630", "UV_OFFLINE": "1"}
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            processes = []
            try:
                for command in (["uvicorn", "nourish_nest.api:app", "--host", "127.0.0.1", "--port", "18630"],
                    ["streamlit", "run", "streamlit_app.py", "--server.address", "127.0.0.1", "--server.port", "18631",
                     "--server.headless", "true", "--browser.gatherUsageStats", "false"]):
                    processes.append(subprocess.Popen([sys.executable, "-m", *command], env=env, creationflags=creationflags))
                print("ISOLATED SERVERS READY. Enter a line to stop and remove temporary data.", flush=True)
                input()
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    process.wait(timeout=20)


if __name__ == "__main__":
    main()
