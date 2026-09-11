from collections.abc import MutableMapping
from typing import Any

from nourish_nest.api_client import Household

PAGES = ("Dashboard", "Household", "Nutrition", "Recipes", "Pantry", "Grocery Lists", "Meal Planner", "AI Assistant")


def sync_household_selection(
    state: MutableMapping[str, Any], households: list[Household]
) -> str | None:
    ids = [str(household.id) for household in households]
    desired = state.pop("pending_household_id", state.get("household_id"))
    selected = desired if desired in ids else (ids[0] if ids else None)
    state["household_id"] = selected
    return selected


def remember_created_household(state: MutableMapping[str, Any], household: Household) -> None:
    state["pending_household_id"] = str(household.id)
    state["success_message"] = f"Created {household.name}. Your household is ready."
    state["pending_page"] = "Dashboard"


def navigate(state: MutableMapping[str, Any], page: str, intent: str = "") -> None:
    if page not in PAGES:
        raise ValueError("Unknown page")
    state["page"] = page
    state["intent"] = intent
