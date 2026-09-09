"""Canonical recipe request hashing; no nutrition or conversion calculations."""

import hashlib
import json
from decimal import Decimal
from uuid import UUID

from nourish_nest.food_schemas import RecipeFields


def canonical_recipe_hash(data: RecipeFields) -> str:
    def canonical(value):
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        return value

    values = data.model_dump(exclude={"expected_version"})
    values["ingredients"].sort(key=lambda row: row["display_order"])
    values["instructions"].sort(key=lambda row: row["step_number"])
    encoded = json.dumps(canonical(values), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
