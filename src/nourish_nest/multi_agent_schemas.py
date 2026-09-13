"""Public preview response excludes the original prompt and private trace payloads."""

from decimal import Decimal
from uuid import UUID

from pydantic import Field

from nourish_nest.assistant_contracts import PlannedDay, StrictModel
from nourish_nest.multi_agent_contracts import (
    VERSION,
    AgentResult,
    ExecutionPlan,
    GroceryEvidence,
    Intent,
    KnowledgeEvidence,
    NutritionEvidence,
    PantryEvidence,
    Status,
    Warning,
)


class MultiAgentRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    member_ids: list[UUID] = Field(default_factory=list, max_length=8)
    include_knowledge: bool = Field(default=True, strict=True)
    knowledge_question: str | None = Field(default=None, min_length=1, max_length=1000)


class StepTrace(StrictModel):
    sequence_number: int
    agent_name: str
    tool_name: str | None = None
    status: str
    duration_ms: Decimal
    failure_code: str | None = None


class TraceSummary(StrictModel):
    agents_executed: int
    tool_calls: int
    duration_ms: Decimal
    partial: bool
    steps: list[StepTrace]
    transitions: list[Status]


class MultiAgentResponse(StrictModel):
    run_id: UUID
    request_id: str
    household_id: UUID
    status: Status
    interpretation: Intent | None
    execution_plan: ExecutionPlan
    meal_plan: list[PlannedDay]
    nutrition_summary: NutritionEvidence | None
    pantry_summary: PantryEvidence | None
    grocery_shortages: GroceryEvidence | None
    knowledge: KnowledgeEvidence
    agent_results: list[AgentResult]
    warnings: list[Warning]
    clarifications: list[str]
    refusal_reason: str | None
    failure_code: str | None
    decision_summary: list[str]
    trace_summary: TraceSummary
    calculation_versions: dict[str, str]
    orchestration_version: str = VERSION
    provider_mode: str = "fake-rule-based"
    preview_only: bool = True
