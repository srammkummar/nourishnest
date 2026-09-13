"""Trusted server-side evidence; no prompts, document bodies or model reasoning."""

from nourish_nest.approval_contracts import canonical, payload_hash
from nourish_nest.models import AgentRunSnapshot


def run_fingerprint(run):
    return payload_hash({
        "id": run.id, "household": run.household_id, "status": run.status,
        "intent": run.interpreted_intent_json, "agents": run.selected_agents_json,
        "calls": run.tool_call_count, "version": run.orchestration_version,
        "warning_count": run.warning_count,
        "completed": run.completed_at, "failure": run.failure_code,
        "steps": [{"agent": s.agent_name, "tool": s.tool_name, "status": s.status,
                   "input": s.input_summary_json, "output": s.output_summary_json,
                   "failure": s.failure_code} for s in sorted(run.steps, key=lambda s: s.sequence_number)]})


def persist_snapshot(session, run, state):
    if state.status != "completed":
        return
    meals = [m for d in state.meal_plan for m in d.meals]
    data = canonical({
        "household_id": state.household_id, "run_id": state.run_id,
        "member_ids": state.member_ids, "intent": run.interpreted_intent_json,
        "meals": sorted(meals, key=lambda m: m.recipe_id),
        "grocery": state.grocery_shortages.shortages if state.grocery_shortages else None,
        "versions": state.calculation_versions,
        "warnings": sorted({w.code for w in state.warnings}), "partial": state.partial,
        "citations": [{"document_id": c.document_id, "chunk_id": c.chunk_id,
                       "excerpt_hash": payload_hash(c.excerpt)} for c in state.knowledge.citations],
        "knowledge_claims": bool(state.knowledge.citations), "run_hash": run_fingerprint(run),
        "completed_at": state.completed_at,
    })
    session.add(AgentRunSnapshot(run_id=run.id, household_id=run.household_id,
                                snapshot_json=data, snapshot_hash=payload_hash(data)))
