"""Offline approval wire contracts and canonical, Decimal-safe serialization."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


def canonical(value):
    if isinstance(value, BaseModel):
        return canonical(value.model_dump())
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite quantity")
        return format(value.normalize(), "f") if value else "0"
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return value


def payload_hash(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProposalRequest(Input):
    title: str = Field(default="AI-assisted weekly grocery list", min_length=1, max_length=200)
    list_name: str = Field(min_length=1, max_length=200)
    expires_in_minutes: int = Field(default=30, ge=1, le=60, strict=True)

    @field_validator("title", "list_name")
    @classmethod
    def whitespace(cls, value):
        return " ".join(value.split())


class DecisionRequest(Input):
    expected_version: int = Field(ge=1, strict=True)


class ApprovalRequest(DecisionRequest):
    confirmation: StrictBool
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("confirmation")
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class ExecutionRequest(DecisionRequest):
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)


class CriticResult(Input):
    decision: Literal["pass", "pass_with_warnings", "block"] = "pass"
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verified_constraints: list[str] = Field(default_factory=list)
    unverifiable_constraints: list[str] = Field(default_factory=lambda: [
        "Ingredient labels and missing allergen metadata require human verification.",
        "Nutrition adequacy and medical suitability are not verified.",
        "Pantry inventory is not reserved; approval identity is not authenticated."])
    checked_calculation_versions: dict[str, str] = Field(default_factory=dict)
    critic_version: Literal["approval-critic-v1"] = "approval-critic-v1"


class ProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    household_id: UUID
    agent_run_id: UUID
    action_type: Literal["create_grocery_list"]
    status: str
    title: str
    canonical_payload_json: dict
    payload_hash: str
    critic_result_json: CriticResult
    warning_count: int
    version: int
    expires_at: datetime
    result_reference_id: UUID | None
    failure_code: str | None


class ExecutionResponse(Input):
    proposal_id: UUID
    status: Literal["completed", "failed"]
    grocery_list_id: UUID | None = None
    list_name: str
    item_count: int = 0
    generation_run_id: UUID | None = None
    failure_code: str | None = None
    replayed: bool = False


class ApprovalPreview(BaseModel):
    """HTTP-only presentation model; no server or ORM imports."""
    run_id: UUID
    household_id: UUID
    status: str
    meal_plan: list[dict]
    nutrition_summary: dict | None
    pantry_summary: dict | None
    grocery_shortages: dict | None
    knowledge: dict
    warnings: list[dict]
    clarifications: list[str]
    refusal_reason: str | None
    trace_summary: dict
