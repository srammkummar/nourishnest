"""Audit only safe identifiers/counts, never prompts, evidence bodies or reasoning."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from time import perf_counter_ns

from nourish_nest.models import AgentRun, AgentStep, utc_now
from nourish_nest.multi_agent_contracts import AGENT_ORDER, VERSION
from nourish_nest.multi_agent_schemas import StepTrace


def elapsed(start: int) -> Decimal:
    return Decimal(perf_counter_ns() - start) / Decimal(1_000_000)


@dataclass
class TraceRecord:
    agent: str
    sequence: int
    tool: str | None
    started_at: datetime = field(default_factory=utc_now)
    start: int = field(default_factory=perf_counter_ns)
    completed_at: datetime | None = None
    status: str = "failed"
    duration_ms: Decimal = Decimal(0)
    failure_code: str | None = None
    input_summary: dict = field(default_factory=dict)
    output_summary: dict = field(default_factory=dict)

    def finish(self, status, code=None):
        self.status, self.failure_code = status, code
        self.completed_at = utc_now()
        self.duration_ms = elapsed(self.start)

    def public(self):
        return StepTrace(sequence_number=self.sequence, agent_name=self.agent,
                         tool_name=self.tool, status=self.status, duration_ms=self.duration_ms,
                         failure_code=self.failure_code)


class TraceRecorder:
    def __init__(self):
        self.records: list[TraceRecord] = []

    def start(self, agent, tool=None):
        index = AGENT_ORDER.index(agent)
        sequence = index * 100 + (sum(r.agent == agent and r.tool is not None
                                     for r in self.records) + 1 if tool else 0)
        record = TraceRecord(agent, sequence, tool)
        self.records.append(record)
        return record

    def ordered(self):
        return sorted(self.records, key=lambda r: r.sequence)

    def persist(self, sessions, state, duration):
        intent = state.interpreted_intent
        safe_intent = intent.model_dump(mode="json", exclude={"knowledge_question", "reason"}) if intent else {}
        with sessions() as session, session.begin():
            run = AgentRun(
                id=state.run_id, request_id=sha256(state.request_id.encode()).hexdigest(),
                household_id=state.household_id, status=state.status.value,
                provider_mode="fake-rule-based", orchestration_version=VERSION,
                interpreted_intent_json=safe_intent,
                selected_agents_json=state.execution_plan.selected_agents,
                tool_call_count=state.tool_call_count, warning_count=len(state.warnings),
                started_at=state.started_at, completed_at=state.completed_at,
                duration_ms=duration, failure_code=state.failure_code,
            )
            run.steps = [AgentStep(
                agent_name=r.agent, sequence_number=r.sequence, status=r.status,
                tool_name=r.tool, input_summary_json=r.input_summary,
                output_summary_json=r.output_summary,
                warning_codes_json=r.output_summary.get("warning_codes", []) +
                ([r.failure_code] if r.failure_code else []),
                started_at=r.started_at, completed_at=r.completed_at or state.completed_at,
                duration_ms=r.duration_ms, failure_code=r.failure_code,
            ) for r in self.ordered()]
            session.add(run)
