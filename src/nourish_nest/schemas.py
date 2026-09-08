import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nourish_nest.domain import ActivityLevel, Goal, Sex
from nourish_nest.models import AllergySeverity, PreferenceType


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    currency: str = Field(default="USD", min_length=3, max_length=3)


class HouseholdResponse(HouseholdCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DietaryPreferenceCreate(BaseModel):
    preference_type: PreferenceType
    value: str = Field(min_length=1, max_length=200)


class DietaryPreferenceResponse(DietaryPreferenceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class AllergyCreate(BaseModel):
    allergen: str = Field(min_length=1, max_length=200)
    severity: AllergySeverity
    notes: str | None = Field(default=None, max_length=2000)


class AllergyResponse(AllergyCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class MemberFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    age: int = Field(ge=13, le=100)
    sex: Sex
    height_cm: float = Field(gt=100, le=250)
    weight_kg: float = Field(gt=30, le=350)
    activity_level: ActivityLevel
    goal: Goal
    weekly_goal_kg: float = Field(default=0.25, ge=0, le=1.0)
    meals_per_day: int = Field(default=3, ge=2, le=6)
    dietary_preferences: list[DietaryPreferenceCreate] = Field(default_factory=list)
    allergies: list[AllergyCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_goal_rate(self) -> "MemberFields":
        if self.goal == Goal.MAINTAIN and self.weekly_goal_kg != 0:
            self.weekly_goal_kg = 0
        if self.goal != Goal.MAINTAIN and self.weekly_goal_kg == 0:
            raise ValueError("weekly_goal_kg must be greater than zero for loss or gain")
        return self


class MemberCreate(MemberFields):
    pass


class MemberUpdate(MemberFields):
    pass


class MemberResponse(MemberFields):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    household_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    dietary_preferences: list[DietaryPreferenceResponse] = Field(default_factory=list)
    allergies: list[AllergyResponse] = Field(default_factory=list)


class MemberListResponse(BaseModel):
    members: list[MemberResponse]