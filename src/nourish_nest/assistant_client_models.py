"""Assistant HTTP wire models. Independent of backend schemas, services, and ORM."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from nourish_nest.grocery_client_models import ShortageResult
from nourish_nest.planning_contracts import RecipeRecommendation, ServingNutrition


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class AssistantInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_message: str = Field(min_length=1, max_length=2000)
    member_id: UUID | None = None
    conversation_context: list[ConversationMessage] = Field(default_factory=list, max_length=6)
    dry_run: Literal[True] = True


class Intent(BaseModel):
    action: Literal["plan", "clarify", "refuse"]
    days: int | None = None
    meal: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    servings: Decimal | None = None
    diets: list[str] = Field(default_factory=list)
    maximum_calories: Decimal | None = None
    strict_calorie_limit: bool = False
    maximum_cooking_minutes: int | None = None
    groceries: bool = False
    compare_target: bool = False
    allow_repeats: bool = False
    reason: str | None = None
    tools: list[str] = Field(default_factory=list)


class Meal(BaseModel):
    slot: str
    recipe_id: UUID
    recipe_name: str
    desired_servings: Decimal


class Day(BaseModel):
    day: int = Field(ge=1, le=7)
    meals: list[Meal]


class Summary(BaseModel):
    daily: list[ServingNutrition]
    weekly: ServingNutrition
    scope: str


class TargetMacros(BaseModel):
    protein_g: int
    carbohydrate_g: int
    fat_g: int


class MemberTarget(BaseModel):
    bmr_calories: int
    maintenance_calories: int
    target_calories: int
    macros: TargetMacros
    calculation_version: str
    warnings: list[str] = Field(default_factory=list)


class Trace(BaseModel):
    name: Literal["recommendations", "nutrition_summary", "member_nutrition", "grocery_shortage"]
    status: Literal["completed"]
    duration_ms: int = Field(ge=0)


class AssistantPreview(BaseModel):
    household_id: UUID
    status: Literal["preview", "clarification"]
    assistant_message: str
    interpreted_constraints: Intent
    proposed_plan: list[Day] = Field(default_factory=list, max_length=7)
    recommendations_used: list[RecipeRecommendation] = Field(default_factory=list)
    nutrition_summary: Summary | None = None
    member_nutrition_target: MemberTarget | None = None
    grocery_shortage_preview: ShortageResult | None = None
    warnings: list[str] = Field(default_factory=list)
    tool_trace: list[Trace] = Field(default_factory=list, max_length=4)
    confirmation_required: bool = False
    calculation_version: str
    model_version: str
    request_id: str
