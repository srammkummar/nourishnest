"""Read-only temporary DB inspection, plus an API replay of the UI execution."""
import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from evidence import call
from setup import MESSAGE, OUT, normal_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["proposed", "approved", "completed"])
    args = parser.parse_args()
    paths = list(Path(tempfile.gettempdir()).glob("nourishnest-product-demo-*/demo.db"))
    assert len(paths) == 1, "Require exactly one isolated demo database"
    path = paths[0].resolve()
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("select name from households").fetchall()[0][0] == "Greenwood Family"
        run = dict(db.execute("select status, provider_mode, selected_agents_json, tool_call_count from agent_runs order by created_at desc").fetchone())
        steps = [dict(r) for r in db.execute("select agent_name, tool_name, status from agent_steps order by sequence_number")]
        proposal = dict(db.execute("select id, payload_hash, status, version from agent_action_proposals order by created_at desc").fetchone())
        execution = db.execute("select * from agent_action_executions").fetchone()
    snapshot = normal_snapshot("sqlite:///"+path.as_posix())["tables"]
    snapshot = {k: v for k, v in snapshot.items() if not k.startswith("agent_")}
    report = {"stage": args.stage, "domain_tables": snapshot, "run": run, "steps": steps,
              "proposal_hash": proposal["payload_hash"], "proposal_status": proposal["status"]}
    if args.stage == "approved":
        previous = json.loads((OUT / "proposed-proof.json").read_text())
        assert previous["domain_tables"] == snapshot
        report["approval_changed_domain_data"] = False
    if args.stage == "completed":
        assert execution is not None and execution["status"] == "completed"
        previous = json.loads((OUT / "approved-proof.json").read_text())
        changed = [k for k in snapshot if snapshot[k] != previous["domain_tables"][k]]
        assert set(changed) == {"grocery_lists", "grocery_list_items", "grocery_generation_runs", "grocery_item_recipe_sources"}
        # Persisted keys are hashed. Use a separate API fixture with a known raw key,
        # rather than misrepresenting a hash as the UI's original retry key.
        preview = call("/assistant/multi-agent-meal-plan-preview", {"message": MESSAGE})
        p = call(f"/assistant/runs/{preview['run_id']}/proposals", {"list_name": "Replay verification only"})
        approved = call(f"/assistant/proposals/{p['id']}/approve", {
            "expected_version": p["version"], "payload_hash": p["payload_hash"], "confirmation": True})
        before_write = normal_snapshot("sqlite:///"+path.as_posix())["tables"]
        assert all(before_write[k] == v for k, v in snapshot.items())
        request = {"expected_version": approved["version"], "payload_hash": p["payload_hash"],
                   "idempotency_key": "product-demo-known-replay-key"}
        first = call(f"/assistant/proposals/{p['id']}/execute", request)
        before_replay = normal_snapshot("sqlite:///"+path.as_posix())["tables"]
        result = call(f"/assistant/proposals/{p['id']}/execute", request)
        assert result["replayed"] and result["grocery_list_id"] == first["grocery_list_id"]
        after = normal_snapshot("sqlite:///"+path.as_posix())["tables"]
        assert all(after[k] == before_replay[k] for k in snapshot)
        report.update(changed_domain_tables=changed, replayed=True, replay_changed_domain_data=False,
                      item_count=result["item_count"], same_grocery_list=True,
                      replay_method="Separate API fixture after the UI capture, with a known raw key")
    (OUT / f"{args.stage}-proof.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"domain_tables", "steps"}}, indent=2))


if __name__ == "__main__":
    main()
