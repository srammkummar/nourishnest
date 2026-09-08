from enum import StrEnum

from pydantic import BaseModel, Field


class TaskType(StrEnum):
    NUTRITION = "nutrition"
    MEAL_PLAN = "meal_plan"
    SHOPPING = "shopping"
    INVENTORY = "inventory"
    CHORES = "chores"


class AgentTask(BaseModel):
    task_type: TaskType
    objective: str
    depends_on: list[TaskType] = Field(default_factory=list)
    requires_approval: bool = False


class WorkflowPlan(BaseModel):
    tasks: list[AgentTask]


def deterministic_route(requested: set[TaskType]) -> WorkflowPlan:
    """Create an auditable task graph before LangGraph executes agent nodes."""
    tasks: list[AgentTask] = []
    if TaskType.INVENTORY in requested or TaskType.MEAL_PLAN in requested:
        tasks.append(AgentTask(task_type=TaskType.INVENTORY, objective="Load pantry inventory"))
    if TaskType.NUTRITION in requested or TaskType.MEAL_PLAN in requested:
        tasks.append(AgentTask(task_type=TaskType.NUTRITION, objective="Calculate nutrition targets"))
    if TaskType.MEAL_PLAN in requested:
        tasks.append(
            AgentTask(
                task_type=TaskType.MEAL_PLAN,
                objective="Generate a constrained meal plan",
                depends_on=[TaskType.INVENTORY, TaskType.NUTRITION],
            )
        )
    if TaskType.SHOPPING in requested:
        dependencies = [TaskType.MEAL_PLAN] if TaskType.MEAL_PLAN in requested else []
        tasks.append(
            AgentTask(
                task_type=TaskType.SHOPPING,
                objective="Create and price a grocery list",
                depends_on=dependencies,
                requires_approval=True,
            )
        )
    if TaskType.CHORES in requested:
        tasks.append(
            AgentTask(
                task_type=TaskType.CHORES,
                objective="Create chore assignments",
                requires_approval=True,
            )
        )
    return WorkflowPlan(tasks=tasks)

