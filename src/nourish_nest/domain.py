from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Sex(StrEnum):
    FEMALE = "female"
    MALE = "male"


class ActivityLevel(StrEnum):
    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    VERY_ACTIVE = "very_active"


class Goal(StrEnum):
    LOSE = "lose"
    MAINTAIN = "maintain"
    GAIN = "gain"


class NutritionProfile(BaseModel):
    age: int = Field(ge=13, le=100)
    sex: Sex
    height_cm: float = Field(gt=100, le=250)
    weight_kg: float = Field(gt=30, le=350)
    activity_level: ActivityLevel
    goal: Goal
    weekly_goal_kg: float = Field(default=0.25, ge=0, le=1.0)
    meals_per_day: int = Field(default=3, ge=2, le=6)

    @model_validator(mode="after")
    def validate_goal_rate(self) -> "NutritionProfile":
        if self.goal == Goal.MAINTAIN and self.weekly_goal_kg != 0:
            self.weekly_goal_kg = 0
        if self.goal != Goal.MAINTAIN and self.weekly_goal_kg == 0:
            raise ValueError("weekly_goal_kg must be greater than zero for loss or gain")
        return self


class MacroTargets(BaseModel):
    protein_g: int
    fat_g: int
    carbohydrate_g: int
    protein_kcal: int
    fat_kcal: int
    carbohydrate_kcal: int


class MealTarget(BaseModel):
    meal_number: int
    calories: int
    protein_g: int
    fat_g: int
    carbohydrate_g: int


class NutritionPlan(BaseModel):
    bmr_calories: int
    maintenance_calories: int
    target_calories: int
    daily_adjustment_calories: int
    macros: MacroTargets
    meals: list[MealTarget]
    warnings: list[str] = Field(default_factory=list)
    calculation_version: str = "mifflin-st-jeor-v1"


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str

