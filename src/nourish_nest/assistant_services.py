"""One bounded read-only coordinator; providers cannot supply database IDs or arguments."""

import asyncio
import html
import json
from decimal import Decimal, localcontext
from time import perf_counter
from types import SimpleNamespace

from pydantic import ValidationError

from nourish_nest.assistant_contracts import (
    AssistantResponse,
    NutritionSummary,
    PlannedDay,
    PlannedMeal,
    PlanningIntent,
    ToolTrace,
)
from nourish_nest.assistant_provider import (
    AssistantError,
    ChatRateLimited,
    expected_tools,
    extract_fake_intent,
    guard_request,
    user_messages,
)
from nourish_nest.config import get_settings
from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.planning_contracts import RecommendationRequest, ServingNutrition
from nourish_nest.planning_services import RecommendationService, dietary_check
from nourish_nest.repositories import HouseholdRepository, MemberRepository, RecipeRepository
from nourish_nest.services import HouseholdService, NotFoundError

NUTRIENTS = ("calories", "protein_g", "carbohydrate_g", "fat_g")
DISCLAIMER = "Nutrition estimates are informational, not medical advice."


def malformed():
    return AssistantError(
        "invalid_assistant_output", "The assistant returned an invalid interpretation. Please retry.",
        502,
    )


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def safe_text(text):
    return html.escape("".join(c for c in text if c.isprintable() or c in "\n\t"))


def summarize(plan, recipes):
    """Scale server-provided per-serving values only; never implement nutrition formulas."""
    daily = []
    for day in plan:
        values = dict.fromkeys(NUTRIENTS, Decimal(0))
        warnings = []
        for meal in day.meals:
            nutrition = recipes[meal.recipe_id].nutrition_per_serving
            warnings.extend(nutrition.warnings)
            for field in NUTRIENTS:
                amount = getattr(nutrition, field)
                if amount is None:
                    values[field] = None
                elif values[field] is not None:
                    values[field] += amount * meal.desired_servings
        daily.append(ServingNutrition(**values, warnings=sorted(set(warnings))))
    totals = {}
    for field in NUTRIENTS:
        totals[field] = (
            None if any(getattr(day, field) is None for day in daily)
            else sum((getattr(day, field) for day in daily), Decimal(0))
        )
    return NutritionSummary(daily=daily, weekly=ServingNutrition(
        **totals, warnings=sorted({w for day in daily for w in day.warnings})
    ))


class MealPlanningAssistant:
    def __init__(self, session, provider, settings=None):
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()

    async def preview(self, household_id, data, request_id):
        # Ownership checks precede provider calls, including disabled-provider responses.
        with self.session.no_autoflush:
            if HouseholdRepository(self.session).get(household_id) is None:
                raise NotFoundError("Household not found")
            member = MemberRepository(self.session).get(household_id, data.member_id) \
                if data.member_id else None
            if data.member_id and member is None:
                raise NotFoundError("Member not found")
            if not data.dry_run:
                raise AssistantError("assistant_preview_only", "Only read-only previews are supported.")
            messages = user_messages(data)
            guard_request(messages)
            try:
                raw = await asyncio.wait_for(
                    self.provider.interpret(messages), self.settings.ai_timeout_seconds
                )
            except TimeoutError:
                raise AssistantError(
                    "assistant_provider_timeout", "The meal assistant timed out. Please retry.", 504
                ) from None
            except ChatRateLimited:
                raise AssistantError(
                    "assistant_provider_rate_limited", "The meal assistant is busy. Please retry later.",
                    429,
                ) from None
            except Exception:  # noqa: BLE001 - untrusted adapter errors must not leak credentials
                raise AssistantError(
                    "assistant_provider_unavailable", "The meal assistant is unavailable.", 503
                ) from None
            try:
                if not isinstance(raw, str) or len(raw) > 8192:
                    raise ValueError("Invalid output size")
                intent = PlanningIntent.model_validate(json.loads(
                    raw, parse_float=Decimal, object_pairs_hook=unique_object
                ))
                # This release supports the fake provider's declared grammar only. It also
                # prevents a replaced/misbehaving provider from dropping explicit constraints
                # or inventing quantities; extending language support needs new evaluations.
                grounded = extract_fake_intent(messages)
                if intent != grounded or intent.tools != expected_tools(intent):
                    raise ValueError("Ungrounded intent")
            except (ValidationError, ValueError, TypeError):
                raise malformed() from None
            if len(intent.tools) > self.settings.ai_max_tool_calls:
                raise AssistantError("assistant_tool_limit", "This request exceeds the tool-call limit.")
            if intent.action == "refuse":
                raise AssistantError("unsafe_assistant_request", "This request cannot be supported safely.")
            response = AssistantResponse(
                household_id=household_id, status="clarification", assistant_message="",
                interpreted_constraints=intent, request_id=request_id,
                model_version="fake-intent-v1", warnings=[DISCLAIMER],
            )
            if intent.action == "clarify":
                response.assistant_message = (
                    "Please specify 1–7 days, one meal slot, and servings per meal. "
                    "For example: Plan 5 vegetarian dinners for 2 people under 600 calories "
                    "per serving, and show what I need to buy. "
                    "This local assistant supports diets, calorie limits, cooking minutes, "
                    "shopping previews, and adult target comparisons. Other constraints "
                    "need clarification. " + DISCLAIMER
                )
                return response
            if intent.compare_target and (member is None or member.age < 18):
                response.assistant_message = (
                    "Select a saved adult member to compare nutrition targets. "
                    "Adult targets are never applied to minors. " + DISCLAIMER
                )
                return response
            with localcontext() as context:
                context.prec = 28
                context.rounding = "ROUND_HALF_EVEN"
                return self._execute(household_id, data, intent, response)

    def _execute(self, household_id, data, intent, response):
        def run(name, callback):
            started = perf_counter()
            value = callback()
            response.tool_trace.append(ToolTrace(
                name=name, status="completed", duration_ms=max(0, int((perf_counter()-started)*1000))
            ))
            return value

        def recommendations():
            result = RecommendationService(self.session).recommend(household_id, RecommendationRequest(
                member_id=data.member_id, maximum_cooking_minutes=intent.maximum_cooking_minutes,
                maximum_missing_ingredients=100, limit=50,
            ))
            response.warnings.extend(w.message for w in result.warnings)
            profile = SimpleNamespace(allergies=[], dietary_preferences=[
                SimpleNamespace(preference_type=SimpleNamespace(value=d), value=d)
                for d in intent.diets
            ])
            eligible = []
            for row in result.recommendations:
                recipe = RecipeRepository(self.session).get_for_household(row.recipe_id, household_id)
                if not dietary_check(recipe, profile)[0]:
                    continue
                nutrition = row.nutrition_per_serving
                if intent.maximum_calories is not None:
                    calories = nutrition.calories
                    if calories is None or nutrition.warnings:
                        response.warnings.append(
                            "Recipes with incomplete nutrition were excluded from the calorie limit."
                        )
                        continue
                    if calories > intent.maximum_calories or (
                        intent.strict_calorie_limit and calories == intent.maximum_calories
                    ):
                        continue
                eligible.append(row)
            return eligible

        candidates = run("recommendations", recommendations)
        if not candidates or (len(candidates) < intent.days and not intent.allow_repeats):
            response.assistant_message = (
                "There are not enough matching recipes for this plan. "
                "Try fewer days, add suitable recipes, or explicitly allow repeats. " + DISCLAIMER
            )
            response.warnings.append("No constraints were relaxed. At most 50 ranked candidates are checked.")
            return response
        chosen = [candidates[index % len(candidates)] for index in range(intent.days)]
        by_id = {r.recipe_id: r for r in chosen}
        response.recommendations_used = list(by_id.values())
        response.proposed_plan = [PlannedDay(day=index+1, meals=[PlannedMeal(
            slot=intent.meal, recipe_id=chosen[index].recipe_id,
            recipe_name=safe_text(chosen[index].recipe_name), desired_servings=intent.servings,
        )] if index < intent.days else []) for index in range(7)]
        response.nutrition_summary = run(
            "nutrition_summary", lambda: summarize(response.proposed_plan, by_id)
        )
        if intent.compare_target:
            response.member_nutrition_target = run("member_nutrition", lambda:
                HouseholdService(self.session).calculate_member_nutrition(household_id, data.member_id))
        if intent.groceries:
            selections = {}
            for row in chosen:
                selections[row.recipe_id] = selections.get(row.recipe_id, Decimal(0)) + intent.servings
            response.grocery_shortage_preview = run("grocery_shortage", lambda:
                GroceryShortageService(self.session).preview(household_id, GroceryRequirementsRequest(
                    recipes=[{"recipe_id": key, "desired_servings": value}
                             for key, value in selections.items()]
                )))
            response.warnings.extend(w.message for w in response.grocery_shortage_preview.warnings)
        response.warnings.extend(w.message for r in chosen for w in r.warnings)
        response.warnings.extend(response.nutrition_summary.weekly.warnings)
        response.warnings.append(
            "Pantry availability is a point-in-time estimate. Nothing is reserved or saved. "
            "Nutrition totals cover all planned servings and only the selected meals."
        )
        response.warnings = sorted({safe_text(w) for w in response.warnings})
        response.status = "preview"
        response.assistant_message = (
            "Here is a meal preview using matching saved recipes, ranked by the existing "
            "pantry coverage and expiring-ingredient score. Nothing has been changed. " + DISCLAIMER
        )
        return response
