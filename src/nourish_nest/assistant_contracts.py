"""Strict, bounded intent and preview contracts; model output never contains tool arguments."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from nourish_nest.domain import NutritionPlan
from nourish_nest.grocery_shortage_schemas import GroceryShortageResponse
from nourish_nest.planning_contracts import RecipeRecommendation, ServingNutrition

ToolName = Literal["recommendations", "nutrition_summary", "member_nutrition", "grocery_shortage"]
Diet = Literal["vegetarian", "vegan", "pescatarian", "halal", "no_beef", "no_pork"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConversationMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class AssistantRequest(StrictModel):
    user_message: str = Field(min_length=1, max_length=2000)
    member_id: UUID | None = None
    conversation_context: list[ConversationMessage] = Field(default_factory=list, max_length=6)
    dry_run: bool = Field(default=True, strict=True)


class PlanningIntent(StrictModel):
    action: Literal["plan", "clarify", "refuse"] = "clarify"
    days: int | None = Field(default=None, ge=1, le=7, strict=True)
    meal: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    servings: Decimal | None = Field(default=None, gt=0, le=100)
    diets: list[Diet] = Field(default_factory=list, max_length=6)
    maximum_calories: Decimal | None = Field(default=None, gt=0, le=10000)
    strict_calorie_limit: bool = False
    maximum_cooking_minutes: int | None = Field(default=None, ge=1, le=1440, strict=True)
    groceries: bool = False
    compare_target: bool = False
    allow_repeats: bool = False
    reason: Literal["missing_inputs", "unsupported_request", "unsafe_request"] | None = None
    tools: list[ToolName] = Field(default_factory=list, max_length=4)


class ToolTrace(StrictModel):
    name: ToolName
    status: Literal["completed"]
    duration_ms: int = Field(ge=0)


class PlannedMeal(StrictModel):
    slot: Literal["breakfast", "lunch", "dinner", "snack"]
    recipe_id: UUID
    recipe_name: str
    desired_servings: Decimal


class PlannedDay(StrictModel):
    day: int = Field(ge=1, le=7)
    meals: list[PlannedMeal] = Field(default_factory=list)


class NutritionSummary(StrictModel):
    daily: list[ServingNutrition]
    weekly: ServingNutrition
    scope: Literal["all planned servings; selected meals only"] = (
        "all planned servings; selected meals only"
    )


class AssistantResponse(StrictModel):
    household_id: UUID
    status: Literal["preview", "clarification"]
    assistant_message: str
    interpreted_constraints: PlanningIntent
    proposed_plan: list[PlannedDay] = Field(default_factory=list)
    recommendations_used: list[RecipeRecommendation] = Field(default_factory=list)
    nutrition_summary: NutritionSummary | None = None
    member_nutrition_target: NutritionPlan | None = None
    grocery_shortage_preview: GroceryShortageResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    tool_trace: list[ToolTrace] = Field(default_factory=list)
    confirmation_required: Literal[False] = False
    calculation_version: Literal["meal-planning-assistant-v1"] = "meal-planning-assistant-v1"
    model_version: str
    request_id: str
