from nourish_nest.orchestration import TaskType, deterministic_route


def test_meal_and_shopping_plan_has_dependencies_and_approval():
    plan = deterministic_route({TaskType.MEAL_PLAN, TaskType.SHOPPING})
    task_types = [task.task_type for task in plan.tasks]
    assert task_types == [
        TaskType.INVENTORY,
        TaskType.NUTRITION,
        TaskType.MEAL_PLAN,
        TaskType.SHOPPING,
    ]
    assert plan.tasks[-1].requires_approval is True
    assert plan.tasks[-1].depends_on == [TaskType.MEAL_PLAN]

