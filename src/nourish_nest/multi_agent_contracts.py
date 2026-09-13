"""Typed state and specialist/tool payloads; no free-form agent messaging."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from nourish_nest.assistant_contracts import Diet, NutritionSummary, PlannedDay, StrictModel
from nourish_nest.domain import NutritionPlan
from nourish_nest.grocery_requirement_schemas import GroceryRequirementsResponse
from nourish_nest.grocery_shortage_schemas import GroceryShortageResponse
from nourish_nest.knowledge_schemas import Citation
from nourish_nest.models import utc_now
from nourish_nest.planning_contracts import RecipeRecommendation

VERSION = "multi-agent-meal-planning-v1"
AgentName = Literal["supervisor", "pantry", "recipe", "knowledge", "nutrition", "grocery"]
AGENT_ORDER = ("supervisor", "pantry", "recipe", "knowledge", "nutrition", "grocery")
Allergen = Literal["peanut", "milk", "egg", "soy", "wheat", "gluten", "tree nut", "fish", "shellfish", "sesame"]
Cuisine = Literal["indian", "italian", "mexican", "mediterranean", "chinese", "thai", "american"]


class Status(StrEnum):
    RECEIVED = "received"
    INTERPRETING = "interpreting"
    CLARIFY = "clarification_required"
    REFUSED = "refused"
    PLANNING = "planning"
    RUNNING = "agents_running"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


TRANSITIONS = {
    Status.RECEIVED: {Status.INTERPRETING, Status.FAILED},
    Status.INTERPRETING: {Status.CLARIFY, Status.REFUSED, Status.PLANNING, Status.FAILED},
    Status.PLANNING: {Status.RUNNING, Status.FAILED},
    Status.RUNNING: {Status.VALIDATING, Status.CLARIFY, Status.FAILED},
    Status.VALIDATING: {Status.COMPLETED, Status.FAILED},
}


class Intent(StrictModel):
    action: Literal["plan", "clarify", "refuse"] = "clarify"
    number_of_meals: int | None = Field(default=None, ge=1, le=7)
    servings: Decimal | None = Field(default=None, gt=0, le=100)
    meal: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    diets: list[Diet] = Field(default_factory=list)
    allergens: list[Allergen] = Field(default_factory=list)
    cuisines: list[Cuisine] = Field(default_factory=list)
    maximum_minutes: int | None = Field(default=None, ge=1, le=1440)
    maximum_missing_ingredients: int = Field(default=100, ge=0, le=100)
    prioritize_expiring: bool = False
    groceries: bool = False
    result_limit: int = Field(default=50, ge=1, le=50)
    knowledge_question: str | None = Field(default=None, max_length=1000)
    reason: str | None = None


class Warning(StrictModel):
    code: str
    message: str
    agent: AgentName | None = None


class ScopeInput(StrictModel):
    household_id: UUID
    member_ids: list[UUID] = Field(default_factory=list, max_length=8)


class MemberEvidence(StrictModel):
    member_id: UUID
    age: int


class ScopeEvidence(StrictModel):
    members: list[MemberEvidence]


class AgentInput(ScopeInput):
    intent: Intent
    include_knowledge: bool = False


class PantryLot(StrictModel):
    item_id: UUID
    food_id: UUID
    quantity: Decimal
    unit: str
    expiration_date: date | None


class PantryEvidence(StrictModel):
    available: list[PantryLot] = Field(default_factory=list)
    expiring: list[PantryLot] = Field(default_factory=list)
    low_stock_food_ids: list[UUID] = Field(default_factory=list)
    expiring_window_days: int


class CandidateEvidence(StrictModel):
    candidates: list[RecipeRecommendation]
    excluded_count: int = 0


class SelectionInput(AgentInput):
    meals: list[PlannedDay]
    candidates: list[RecipeRecommendation]


class MemberTarget(StrictModel):
    member_id: UUID
    target: NutritionPlan
    planned_calories_per_person: Decimal | None
    daily_calorie_differences: list[Decimal | None] = Field(default_factory=list)
    comparison_scope: str = "Per-person selected meals versus an adult full-day target; not diet adequacy."


class NutritionEvidence(StrictModel):
    summary: NutritionSummary
    targets: list[MemberTarget] = Field(default_factory=list)
    disclaimer: str = "Nutrition estimates are informational, not medical advice."


class GroceryEvidence(StrictModel):
    requirements: GroceryRequirementsResponse | None = None
    shortages: GroceryShortageResponse | None = None


class KnowledgeEvidence(StrictModel):
    answer: str = "No approved evidence was found."
    citations: list[Citation] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)


class AgentResult(StrictModel):
    agent: AgentName
    status: Literal["completed", "failed", "cancelled"]
    allowed_tools: list[str]
    warnings: list[Warning] = Field(default_factory=list)
    failure_code: str | None = None
    started_at: datetime
    completed_at: datetime
    duration_ms: Decimal
    pantry: PantryEvidence | None = None
    recipes: CandidateEvidence | None = None
    nutrition: NutritionEvidence | None = None
    grocery: GroceryEvidence | None = None
    knowledge: KnowledgeEvidence | None = None


class ExecutionPlan(StrictModel):
    selected_agents: list[AgentName] = Field(default_factory=list)
    parallel: list[AgentName] = Field(default_factory=list)
    dependent: list[AgentName] = Field(default_factory=list)


class RunState(StrictModel):
    request_id: str
    run_id: UUID = Field(default_factory=uuid4)
    household_id: UUID
    member_ids: list[UUID]
    original_request: str = Field(repr=False, exclude=True)
    interpreted_intent: Intent | None = None
    constraints: Intent | None = None
    execution_plan: ExecutionPlan = Field(default_factory=ExecutionPlan)
    tool_call_count: int = 0
    tool_call_limit: int
    candidate_recipes: list[RecipeRecommendation] = Field(default_factory=list)
    meal_plan: list[PlannedDay] = Field(default_factory=list)
    pantry_evidence: PantryEvidence | None = None
    nutrition_results: NutritionEvidence | None = None
    grocery_shortages: GroceryEvidence | None = None
    knowledge: KnowledgeEvidence = Field(default_factory=KnowledgeEvidence)
    warnings: list[Warning] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    refusal_reason: str | None = None
    status: Status = Status.RECEIVED
    transitions: list[Status] = Field(default_factory=lambda: [Status.RECEIVED])
    agent_results: list[AgentResult] = Field(default_factory=list)
    partial: bool = False
    failure_code: str | None = None
    calculation_versions: dict[str, str] = Field(default_factory=lambda: {
        "recommendations": "recipe-recommendations-v1", "nutrition": "meal-planning-assistant-v1",
        "grocery": "grocery-shortage-v1", "knowledge": "knowledge-retrieval-v1"})
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    def transition(self, status: Status):
        if status not in TRANSITIONS.get(self.status, set()):
            raise ValueError(f"Invalid workflow transition {self.status} -> {status}")
        self.status = status
        self.transitions.append(status)
