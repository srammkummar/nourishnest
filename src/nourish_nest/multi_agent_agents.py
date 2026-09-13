"""Specialists choose authorized tools and produce typed evidence, not arbitrary prose."""

from nourish_nest.assistant_contracts import PlannedDay, PlannedMeal
from nourish_nest.multi_agent_contracts import (
    AgentInput,
    CandidateEvidence,
    ExecutionPlan,
    GroceryEvidence,
    Intent,
    KnowledgeEvidence,
    NutritionEvidence,
    PantryEvidence,
    SelectionInput,
)
from nourish_nest.multi_agent_tools import WorkflowError
from nourish_nest.planning_contracts import RecipeRecommendation


class SupervisorAgent:
    name = "supervisor"
    responsibility = "Validate intent, choose bounded stages, allocate unique meal slots and merge evidence."

    def plan(self, data: AgentInput, limit: int) -> ExecutionPlan:
        intent = data.intent
        if intent.action != "plan" or not intent.number_of_meals or not intent.servings or not intent.meal:
            raise WorkflowError("tool_validation_error")
        independent = ["pantry", "recipe"] + (["knowledge"] if data.include_knowledge else [])
        dependent = ["nutrition"] + (["grocery"] if intent.groceries else [])
        agents = ["supervisor", *independent, *dependent]
        if len(agents) > limit:
            raise WorkflowError("agent_budget_exceeded")
        return ExecutionPlan(selected_agents=agents, parallel=independent, dependent=dependent)

    def select(self, intent: Intent, candidates: list[RecipeRecommendation]) -> list[PlannedDay]:
        if len(candidates) < intent.number_of_meals:
            return []
        return [PlannedDay(day=i+1, meals=[PlannedMeal(
            slot=intent.meal, recipe_id=row.recipe_id, recipe_name=row.recipe_name,
            desired_servings=intent.servings)])
            for i, row in enumerate(candidates[:intent.number_of_meals])]


class PantryAgent:
    name = "pantry"
    responsibility = "Collect current availability, expiring lots and low-stock evidence without expiry writes."

    async def execute(self, data: AgentInput, tools) -> tuple[str, PantryEvidence]:
        result = await tools.call(self.name, "read_pantry_summary", data)
        result.expiring = (await tools.call(self.name, "read_expiring_inventory", data)).expiring
        return "pantry", result


class RecipeAgent:
    name = "recipe"
    responsibility = "Apply hard structural constraints to ranked deterministic recipe candidates."

    async def execute(self, data: AgentInput, tools) -> tuple[str, CandidateEvidence]:
        return "recipes", await tools.call(self.name, "recommend_recipes", data)


class KnowledgeAgent:
    name = "knowledge"
    responsibility = "Retrieve explanatory evidence once and preserve citations and injection warnings."

    async def execute(self, data: AgentInput, tools) -> tuple[str, KnowledgeEvidence]:
        return "knowledge", await tools.call(self.name, "retrieve_knowledge", data)


class NutritionAgent:
    name = "nutrition"
    responsibility = "Aggregate existing nutrition results and compare selected adult targets."

    async def execute(self, data: SelectionInput, tools) -> tuple[str, NutritionEvidence]:
        tool = "get_member_nutrition_target" if data.member_ids else "calculate_recipe_nutrition"
        return "nutrition", await tools.call(self.name, tool, data)


class GroceryAgent:
    name = "grocery"
    responsibility = "Preview combined requirements and shortages after unique recipe selection."

    async def execute(self, data: SelectionInput, tools) -> tuple[str, GroceryEvidence]:
        required = await tools.call(self.name, "preview_recipe_requirements", data)
        missing = await tools.call(self.name, "preview_grocery_shortages", data)
        return "grocery", GroceryEvidence(requirements=required, shortages=missing)


SPECIALISTS = {agent.name: agent for agent in (
    PantryAgent(), RecipeAgent(), KnowledgeAgent(), NutritionAgent(), GroceryAgent())}
