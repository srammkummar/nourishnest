"""Provider-independent knowledge vocabulary."""

from enum import StrEnum


class SourceType(StrEnum):
    DOCUMENTATION = "nourishnest_documentation"
    NUTRITION = "nutrition_guidance"
    FOOD_SAFETY = "food_safety_guidance"
    RECIPE = "recipe_document"
    USER = "user_document"


class Visibility(StrEnum):
    GLOBAL = "global"
    HOUSEHOLD = "household"


class DocumentStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"
