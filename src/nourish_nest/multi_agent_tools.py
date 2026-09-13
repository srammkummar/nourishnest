"""Allowlisted tools. Every worker owns a read-only connection and Session."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from threading import Event
from types import SimpleNamespace

from pydantic import BaseModel, ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from nourish_nest.assistant_services import summarize
from nourish_nest.grocery_requirement_schemas import (
    GroceryRequirementsRequest,
    GroceryRequirementsResponse,
)
from nourish_nest.grocery_requirement_services import GroceryRequirementsService
from nourish_nest.grocery_shortage_schemas import GroceryShortageResponse
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.knowledge_schemas import RetrievalRequest
from nourish_nest.knowledge_services import KnowledgeService
from nourish_nest.models import PantryItemStatus, utc_now
from nourish_nest.multi_agent_contracts import (
    AgentInput,
    CandidateEvidence,
    KnowledgeEvidence,
    MemberEvidence,
    MemberTarget,
    NutritionEvidence,
    PantryEvidence,
    PantryLot,
    ScopeEvidence,
    ScopeInput,
    SelectionInput,
    Warning,
)
from nourish_nest.pantry_repositories import PantryRepository
from nourish_nest.pantry_services import PantryService
from nourish_nest.planning_contracts import RecommendationRequest
from nourish_nest.planning_services import RecommendationService, dietary_check, serving_nutrition
from nourish_nest.repositories import HouseholdRepository, MemberRepository, RecipeRepository
from nourish_nest.services import HouseholdService, NotFoundError


class WorkflowError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def canonical_allergen(value):
    return " ".join(value.casefold().strip().split()).removesuffix("s")


def scope(session, data):
    if HouseholdRepository(session).get(data.household_id) is None:
        raise NotFoundError("Household not found")
    members = []
    for identifier in data.member_ids:
        member = MemberRepository(session).get(data.household_id, identifier)
        if member is None:
            raise NotFoundError("Member not found")
        members.append(MemberEvidence(member_id=member.id, age=member.age))
    return ScopeEvidence(members=members)


def pantry(session, data):
    repo = PantryRepository(session)
    today = utc_now().date()
    lots = [PantryLot(item_id=lot.id, food_id=lot.food_id, quantity=lot.quantity,
                      unit=lot.unit, expiration_date=lot.expiration_date)
            for lot in repo.items(data.household_id, {PantryItemStatus.ACTIVE})
            if lot.quantity > 0 and (lot.expiration_date is None or lot.expiration_date >= today)]
    # This existing method is a read-only calculation; summary/expiring methods are not used.
    low = PantryService(session).low_stock(data.household_id, as_of=today)
    return PantryEvidence(available=sorted(lots, key=lambda l: l.item_id),
                          low_stock_food_ids=sorted({r.food_id for r in low}),
                          expiring_window_days=7)


def expiring(session, data):
    lots = PantryRepository(session).expiring(data.household_id, utc_now().date() + timedelta(days=7))
    return PantryEvidence(expiring=[PantryLot(
        item_id=lot.id, food_id=lot.food_id, quantity=lot.quantity, unit=lot.unit,
        expiration_date=lot.expiration_date) for lot in sorted(lots, key=lambda l: l.id)
        if lot.quantity > 0], expiring_window_days=7)


def eligible(recipe, data, members):
    if not recipe or not recipe.ingredients:
        return False
    intent = data.intent
    if intent.maximum_minutes is not None and (
        recipe.preparation_minutes + recipe.cooking_minutes > intent.maximum_minutes
    ):
        return False
    if intent.cuisines and (recipe.cuisine or "").casefold() not in intent.cuisines:
        return False
    profile = SimpleNamespace(allergies=[], dietary_preferences=[
        SimpleNamespace(preference_type=SimpleNamespace(value=d), value=d) for d in intent.diets])
    profiles = [profile, *members]
    exclusions = {canonical_allergen(a) for a in intent.allergens}
    exclusions.update(canonical_allergen(a.allergen) for m in members for a in m.allergies)
    if any(canonical_allergen(a.allergen) in exclusions and a.relationship_type in {"contains", "may_contain"}
           for ingredient in recipe.ingredients for a in ingredient.food.allergens):
        return False
    return all(dietary_check(recipe, p)[0] for p in profiles)


def members_for(session, data):
    members = [MemberRepository(session).get(data.household_id, mid) for mid in data.member_ids]
    if any(m is None for m in members):
        raise WorkflowError("household_isolation_failed")
    if any(p.preference_type.value == "custom" for m in members for p in m.dietary_preferences):
        raise WorkflowError("unsupported_member_preference")
    return members


def complete_nutrition(value):
    # Legacy calculations report partial/zero-filled values with warnings. Preserve that
    # API, but withhold uncertain numbers in this preview instead of inventing completeness.
    if value.warnings:
        return value.model_copy(update={
            "calories": None, "protein_g": None, "carbohydrate_g": None, "fat_g": None,
            "warnings": ["Nutrition value unavailable: source data or conversions are incomplete."]})
    return value


def recommendations(session, data):
    rows = RecommendationService(session).recommend(data.household_id, RecommendationRequest(
        maximum_missing_ingredients=data.intent.maximum_missing_ingredients, limit=50),
        desired_servings=data.intent.servings, expiring_soon_days=7).recommendations
    members = members_for(session, data)
    repo = RecipeRepository(session)
    chosen = [r for r in rows if eligible(repo.get_for_household(r.recipe_id, data.household_id), data, members)]
    for row in chosen:
        row.nutrition_per_serving = complete_nutrition(row.nutrition_per_serving)
    # Retain Phase 7 scores. Expiring preference is a stable partition, not another scoring formula.
    chosen.sort(key=lambda r: (
        -int(bool(r.expiring_ingredients)) if data.intent.prioritize_expiring else 0,
        -r.score.total, r.recipe_name.casefold(), r.recipe_id))
    return CandidateEvidence(candidates=chosen[:data.intent.result_limit], excluded_count=len(rows)-len(chosen))


def validate_selection(session, data):
    members = members_for(session, data)
    identifiers = [m.recipe_id for day in data.meals for m in day.meals]
    if len(identifiers) != len(set(identifiers)):
        raise WorkflowError("constraint_validation_failed")
    for identifier in identifiers:
        recipe = RecipeRepository(session).get_for_household(identifier, data.household_id)
        if not eligible(recipe, data, members):
            raise WorkflowError("constraint_validation_failed")
    return ScopeEvidence(members=[MemberEvidence(member_id=m.id, age=m.age) for m in members])


def nutrition(session, data):
    rows = {}
    for candidate in data.candidates:
        recipe = RecipeRepository(session).get_for_household(candidate.recipe_id, data.household_id)
        if recipe is None:
            raise WorkflowError("household_isolation_failed")
        rows[candidate.recipe_id] = candidate.model_copy(update={
            "nutrition_per_serving": complete_nutrition(serving_nutrition(recipe))})
    return NutritionEvidence(summary=summarize(data.meals, rows))


def targets(session, data):
    result = nutrition(session, data)
    for identifier in data.member_ids:
        target = HouseholdService(session).calculate_member_nutrition(data.household_id, identifier)
        calories = result.summary.weekly.calories
        result.targets.append(MemberTarget(member_id=identifier, target=target,
                              planned_calories_per_person=calories / data.intent.servings
                              if calories is not None else None,
                              daily_calorie_differences=[
                                  day.calories / data.intent.servings - Decimal(target.target_calories)
                                  if day.calories is not None else None for day in result.summary.daily]))
    return result


def selection_request(data):
    return GroceryRequirementsRequest(recipes=[
        {"recipe_id": meal.recipe_id, "desired_servings": meal.desired_servings}
        for day in data.meals for meal in day.meals])


def requirements(session, data):
    return GroceryRequirementsService(session).preview(data.household_id, selection_request(data))


def shortages(session, data):
    return GroceryShortageService(session).preview(data.household_id, selection_request(data))


def knowledge(session, data):
    result = KnowledgeService(session).retrieve(data.household_id, RetrievalRequest(
        query=data.intent.knowledge_question or "food storage safe handling", top_k=3, candidate_count=20))
    return KnowledgeEvidence(
        answer="Approved evidence is quoted in the citations below." if result.results else "No approved evidence was found.",
        citations=result.results,
        warnings=[Warning(code=w.code, message=w.message, agent="knowledge") for w in result.warnings])


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    allowed_agent: str
    request_schema: type[BaseModel]
    response_schema: type[BaseModel]
    handler: object
    classification: str = "read"
    timeout_policy: str = "per-agent remaining deadline, bounded worker cleanup"


TOOL_SPECS = tuple(ToolSpec(*args) for args in (
    ("validate_scope", "Validate household and members", "supervisor", ScopeInput, ScopeEvidence, scope),
    ("validate_selection", "Recheck scoped recipes and hard exclusions", "supervisor", SelectionInput, ScopeEvidence, validate_selection),
    ("read_pantry_summary", "Read lots and existing low-stock calculation", "pantry", AgentInput, PantryEvidence, pantry),
    ("read_expiring_inventory", "Read lots expiring within seven days", "pantry", AgentInput, PantryEvidence, expiring),
    ("recommend_recipes", "Filter existing deterministic recommendations", "recipe", AgentInput, CandidateEvidence, recommendations),
    ("calculate_recipe_nutrition", "Use existing nutrition and aggregation", "nutrition", SelectionInput, NutritionEvidence, nutrition),
    ("get_member_nutrition_target", "Compare saved adult targets", "nutrition", SelectionInput, NutritionEvidence, targets),
    ("preview_recipe_requirements", "Use existing requirement preview", "grocery", SelectionInput, GroceryRequirementsResponse, requirements),
    ("preview_grocery_shortages", "Use existing shortage preview", "grocery", SelectionInput, GroceryShortageResponse, shortages),
    ("retrieve_knowledge", "Retrieve scoped Phase 10A citations once", "knowledge", AgentInput, KnowledgeEvidence, knowledge),
))


class ToolRegistry:
    def __init__(self, engine, state, trace, timeout):
        self.engine, self.state, self.trace, self.timeout = engine, state, trace, timeout
        self.specs = {s.name: s for s in TOOL_SPECS}
        self.workers: set[asyncio.Task] = set()
        self.used: set[str] = set()

    def allowed(self, agent):
        return [s.name for s in self.specs.values() if s.allowed_agent == agent]

    def _worker(self, spec, data, cancelled):
        # Return a safe outcome instead of raising through a cancelled shielded future.
        # Python may log those exceptions (including SQL parameters) before they are drained.
        try:
            return self._read(spec, data, cancelled), None
        except NotFoundError:
            return None, "not_found"
        except WorkflowError as error:
            return None, error.code
        except Exception:  # noqa: BLE001 - no private exception crosses the thread boundary
            return None, "agent_timeout" if cancelled.is_set() else "orchestration_failed"

    def _read(self, spec, data, cancelled):
        with self.engine.connect() as connection:
            sqlite = connection.dialect.name == "sqlite"
            raw = connection.connection.driver_connection if sqlite else None
            def check_cancel(*_):
                if cancelled.is_set():
                    raise WorkflowError("agent_timeout")
            if sqlite:
                connection.exec_driver_sql("PRAGMA query_only=ON")
                raw.set_progress_handler(lambda: int(cancelled.is_set()), 1000)
            else:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            event.listen(connection, "before_cursor_execute", check_cancel)
            try:
                with Session(bind=connection) as session, session.no_autoflush, localcontext() as ctx:
                    ctx.prec, ctx.rounding = 28, "ROUND_HALF_EVEN"
                    check_cancel()
                    result = spec.handler(session, data)
                    return spec.response_schema.model_validate(result.model_dump())
            finally:
                event.remove(connection, "before_cursor_execute", check_cancel)
                connection.rollback()
                if sqlite:
                    raw.set_progress_handler(None, 0)
                    connection.exec_driver_sql("PRAGMA query_only=OFF")
                    connection.rollback()

    async def call(self, agent, name, arguments):
        record = self.trace.start(agent, name if name in self.specs else "unknown_tool")
        cancelled = Event()
        task = None
        try:
            spec = self.specs.get(name)
            if spec is None or spec.allowed_agent != agent or spec.classification != "read":
                raise WorkflowError("unauthorized_tool")
            try:
                data = spec.request_schema.model_validate(arguments.model_dump() if isinstance(arguments, BaseModel) else arguments)
            except (ValidationError, TypeError):
                raise WorkflowError("tool_validation_error") from None
            if data.household_id != self.state.household_id or data.member_ids != self.state.member_ids:
                raise WorkflowError("household_isolation_failed")
            if self.state.tool_call_count >= self.state.tool_call_limit:
                raise WorkflowError("tool_budget_exceeded")
            if name in self.used:
                raise WorkflowError("tool_budget_exceeded")
            self.used.add(name)
            self.state.tool_call_count += 1
            record.input_summary = {"household_id": str(data.household_id), "member_count": len(data.member_ids)}
            if isinstance(data, SelectionInput):
                record.input_summary["recipe_ids"] = [str(m.recipe_id) for d in data.meals for m in d.meals]
            task = asyncio.create_task(asyncio.to_thread(self._worker, spec, data, cancelled))
            self.workers.add(task)
            result, failure = await asyncio.wait_for(asyncio.shield(task), self.timeout)
            if failure == "not_found":
                raise NotFoundError("Household or member not found")
            if failure:
                raise WorkflowError(failure)
            record.output_summary = {"validated": True}
            if isinstance(result, CandidateEvidence):
                record.output_summary["recipe_ids"] = [str(c.recipe_id) for c in result.candidates]
                record.output_summary["excluded_count"] = result.excluded_count
            elif isinstance(result, KnowledgeEvidence):
                record.output_summary["chunk_ids"] = [str(c.chunk_id) for c in result.citations]
            elif isinstance(result, PantryEvidence):
                record.output_summary["available_lot_count"] = len(result.available)
                record.output_summary["expiring_lot_count"] = len(result.expiring)
            record.finish("completed")
            return result
        except TimeoutError:
            record.finish("failed", "agent_timeout")
            raise WorkflowError("agent_timeout") from None
        except asyncio.CancelledError:
            record.finish("cancelled", "agent_timeout")
            raise
        except WorkflowError as error:
            record.finish("failed", error.code)
            raise
        except NotFoundError:
            record.finish("failed", "household_isolation_failed")
            raise
        except Exception:  # noqa: BLE001 - tool exceptions can contain private SQL parameters
            record.finish("failed", "orchestration_failed")
            raise WorkflowError("orchestration_failed") from None
        finally:
            cancelled.set()
            if task is not None:
                # Drain the worker so no hidden database activity outlives the response.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:  # noqa: BLE001 - original failure already recorded above
                        break
                self.workers.discard(task)
