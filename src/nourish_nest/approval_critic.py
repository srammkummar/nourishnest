"""Read-only deterministic critic. No write registry, provider or plan repair."""

from datetime import UTC, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from nourish_nest.approval_contracts import CriticResult, canonical, payload_hash
from nourish_nest.approval_snapshot import run_fingerprint
from nourish_nest.grocery_generation_services import GroceryGenerationError, _persistable
from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest
from nourish_nest.grocery_requirement_services import GroceryRequirementsService
from nourish_nest.grocery_shortage_schemas import GroceryShortageResponse
from nourish_nest.knowledge_repositories import KnowledgeRepository
from nourish_nest.models import AgentRun, AgentRunSnapshot, utc_now
from nourish_nest.multi_agent_contracts import VERSION, AgentInput, Intent
from nourish_nest.multi_agent_tools import (
    TOOL_SPECS,
    WorkflowError,
    canonical_allergen,
    eligible,
    members_for,
)
from nourish_nest.repositories import RecipeRepository
from nourish_nest.services import NotFoundError

VERSIONS = {"recommendations": "recipe-recommendations-v1", "nutrition": "meal-planning-assistant-v1",
            "grocery": "grocery-shortage-v1", "knowledge": "knowledge-retrieval-v1"}


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def load_source(session, household_id, run_id):
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.household_id == household_id))
    if run is None:
        raise NotFoundError("Agent run not found")
    snapshot = session.scalar(select(AgentRunSnapshot).where(
        AgentRunSnapshot.run_id == run_id, AgentRunSnapshot.household_id == household_id))
    return run, snapshot


class CriticAgent:
    allowed_tools = ()

    def review(self, session, household_id, run_id, *, action="create_grocery_list", now=None):
        now = now or utc_now()
        result = CriticResult()
        def block(code):
            result.blocking_issues.append(code)
        with session.no_autoflush:
            run, saved = load_source(session, household_id, run_id)
            if action != "create_grocery_list":
                block("prohibited_action")
            if run.status != "completed" or run.failure_code:
                block("unsuccessful_source_run")
            if saved is None:
                block("missing_source_snapshot")
                return self._finish(result)
            data = saved.snapshot_json
            if payload_hash(data) != saved.snapshot_hash or data.get("run_hash") != run_fingerprint(run):
                block("changed_source_run")
            if data.get("household_id") != str(household_id) or data.get("run_id") != str(run_id):
                block("household_isolation_failed")
            if not timedelta(0) <= now - aware(run.completed_at) <= timedelta(minutes=60):
                block("stale_preview")
            calls = [s for s in run.steps if s.tool_name]
            specs = {s.name: s for s in TOOL_SPECS}
            agents = run.selected_agents_json
            if (not 1 <= len(agents) <= 6 or len(agents) != len(set(agents))
                    or run.tool_call_count != len(calls) or not 1 <= len(calls) <= 12
                    or len({s.tool_name for s in calls}) != len(calls)
                    or any(s.tool_name not in specs or specs[s.tool_name].allowed_agent != s.agent_name
                           or specs[s.tool_name].classification != "read" for s in calls)):
                block("agent_tool_budget_violation")
            required = {"supervisor", "pantry", "recipe", "nutrition", "grocery"}
            completed = {s.agent_name for s in run.steps if not s.tool_name and s.status == "completed"}
            if not required <= completed or any(s.status != "completed" and s.agent_name != "knowledge" for s in run.steps):
                block("failed_required_agent")
            if data.get("partial"):
                result.warnings.append("optional_knowledge_unavailable")
            result.warnings.extend(data.get("warnings", []))
            if run.orchestration_version != VERSION or data.get("versions") != VERSIONS:
                block("calculation_version_mismatch")
            result.checked_calculation_versions = data.get("versions", {})
            try:
                self._content(session, household_id, data, result)
            except (ValueError, KeyError, TypeError, ValidationError, InvalidOperation):
                block("invalid_snapshot")
            except NotFoundError:
                block("household_isolation_failed")
            except GroceryGenerationError:
                block("invalid_quantity")
            except WorkflowError:
                block("unsupported_member_constraint")
        return self._finish(result)

    @staticmethod
    def _finish(result):
        result.blocking_issues = sorted(set(result.blocking_issues))
        result.warnings = sorted(set(result.warnings))
        result.decision = "block" if result.blocking_issues else "pass_with_warnings" if result.warnings else "pass"
        if not result.blocking_issues:
            result.verified_constraints = ["Source run and snapshot integrity", "Household and member ownership",
                "Read-only agent/tool budgets", "Stored allergies and dietary constraints", "Meal count and servings",
                "Distinct accessible recipes", "Representable quantities and grocery lineage",
                "Calculation versions", "Citation integrity and scope", "Action allowlist and preview age"]
        return result

    @staticmethod
    def _content(session, household_id, data, result):
        block = result.blocking_issues.append
        intent = Intent.model_validate(data["intent"])
        if intent.action != "plan" or not intent.groceries:
            block("prohibited_or_missing_grocery_action")
        args = AgentInput(household_id=household_id, member_ids=data["member_ids"], intent=intent,
                          include_knowledge=False)
        # Explicit scope reads; never accept member identities from the approval request.
        from nourish_nest.multi_agent_tools import scope
        scope(session, args)
        members = members_for(session, args)
        if any(m.age < 18 for m in members):
            block("unsafe_request")
        meals = data["meals"]
        ids = [m["recipe_id"] for m in meals]
        if len(ids) != len(set(ids)):
            block("duplicate_recipes")
        if len(meals) != intent.number_of_meals or any(
                Decimal(m["desired_servings"]) != intent.servings or m["slot"] != intent.meal for m in meals):
            block("meal_count_or_servings_mismatch")
        exclusions = {canonical_allergen(a) for a in intent.allergens}
        exclusions.update(canonical_allergen(a.allergen) for m in members for a in m.allergies)
        for meal in meals:
            _persistable(Decimal(meal["desired_servings"]))
            recipe = RecipeRepository(session).get_for_household(UUID(meal["recipe_id"]), household_id)
            if recipe is None:
                block("household_isolation_failed")
                continue
            if recipe.name != meal["recipe_name"]:
                block("changed_recipe")
            if any(canonical_allergen(a.allergen) in exclusions and a.relationship_type in {"contains", "may_contain"}
                   for i in recipe.ingredients for a in i.food.allergens):
                block("allergy_conflict")
            if not eligible(recipe, args, members):
                block("dietary_or_recipe_constraint_conflict")
        if result.blocking_issues:
            return
        if not data.get("grocery"):
            block("missing_grocery_lineage")
            return
        grocery = GroceryShortageResponse.model_validate(data["grocery"])
        selections = GroceryRequirementsRequest(recipes=[
            {"recipe_id": m["recipe_id"], "desired_servings": m["desired_servings"]} for m in meals])
        if grocery.household_id != household_id or canonical(grocery.recipes) != canonical(selections.recipes):
            block("household_or_grocery_selection_mismatch")
        fresh = GroceryRequirementsService(session).preview(household_id, selections)
        if fresh.warnings:
            block("incomplete_grocery_calculation")
        actual = [{k: v for k, v in canonical(r).items() if k in
                   {"food_id", "food_name", "required_quantity", "canonical_unit", "sources"}} for r in grocery.requirements]
        if actual != canonical(fresh.requirements):
            block("changed_or_missing_grocery_lineage")
        for r in grocery.requirements:
            for q in (r.required_quantity, r.available_quantity, r.shortage_quantity,
                      *(s.required_quantity for s in r.sources)):
                try:
                    _persistable(q)
                except Exception:  # noqa: BLE001 - quantities produce a safe structured issue
                    block("invalid_quantity")
            if not r.sources or r.shortage_quantity != max(Decimal(0), r.required_quantity-r.available_quantity):
                block("invalid_shortage_or_lineage")
            if r.purchase_required != (r.shortage_quantity > 0):
                block("invalid_shortage_or_lineage")
            # Lot ownership is checked even though no lot will be mutated.
            from nourish_nest.models import PantryItem
            for lot in r.pantry_lots:
                stored = session.get(PantryItem, lot.pantry_item_id)
                if stored is None or stored.household_id != household_id or stored.food_id != r.food_id:
                    block("household_isolation_failed")
        if data.get("knowledge_claims") and not data.get("citations"):
            block("missing_citation")
        chunks = {str(c.id): c for c in KnowledgeRepository(session).visible_chunks(household_id, None)}
        for citation in data["citations"]:
            chunk = chunks.get(citation["chunk_id"])
            if (chunk is None or str(chunk.document_id) != citation["document_id"]
                    or payload_hash(chunk.content) != citation["excerpt_hash"]):
                block("citation_integrity_or_scope_failed")
