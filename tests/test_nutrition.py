import pytest

from nourish_nest.domain import ActivityLevel, Goal, NutritionProfile, Sex
from nourish_nest.nutrition import UnsupportedProfileError, calculate_nutrition_plan


def profile(**overrides) -> NutritionProfile:
    values = {
        "age": 35,
        "sex": Sex.FEMALE,
        "height_cm": 165,
        "weight_kg": 68,
        "activity_level": ActivityLevel.MODERATE,
        "goal": Goal.LOSE,
        "weekly_goal_kg": 0.25,
        "meals_per_day": 3,
    }
    values.update(overrides)
    return NutritionProfile(**values)


def test_calculation_is_deterministic_and_allocations_reconcile():
    result = calculate_nutrition_plan(profile())
    assert result.bmr_calories == 1375
    assert result.maintenance_calories == 2132
    assert result.target_calories == 1857
    assert sum(meal.calories for meal in result.meals) == result.target_calories
    assert sum(meal.protein_g for meal in result.meals) == result.macros.protein_g


def test_maintenance_ignores_weekly_rate():
    result = calculate_nutrition_plan(profile(goal=Goal.MAINTAIN, weekly_goal_kg=0.5))
    assert result.target_calories == result.maintenance_calories
    assert result.daily_adjustment_calories == 0


def test_low_target_is_clamped_and_warned():
    result = calculate_nutrition_plan(
        profile(weight_kg=45, height_cm=150, activity_level=ActivityLevel.SEDENTARY, weekly_goal_kg=1)
    )
    assert result.target_calories == 1200
    assert result.warnings


def test_minor_is_rejected():
    with pytest.raises(UnsupportedProfileError):
        calculate_nutrition_plan(profile(age=17))
