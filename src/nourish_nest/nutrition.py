from nourish_nest.domain import (
    ActivityLevel,
    Goal,
    MacroTargets,
    MealTarget,
    NutritionPlan,
    NutritionProfile,
    Sex,
)

ACTIVITY_FACTORS = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.VERY_ACTIVE: 1.725,
}

MIN_CALORIES = {Sex.FEMALE: 1200, Sex.MALE: 1500}
KCAL_PER_KG = 7700


class UnsupportedProfileError(ValueError):
    """Raised when the current calculator cannot safely support a profile."""


def calculate_bmr(profile: NutritionProfile) -> float:
    base = 10 * profile.weight_kg + 6.25 * profile.height_cm - 5 * profile.age
    return base - 161 if profile.sex == Sex.FEMALE else base + 5


def _goal_adjustment(profile: NutritionProfile) -> int:
    if profile.goal == Goal.MAINTAIN:
        return 0
    adjustment = round(profile.weekly_goal_kg * KCAL_PER_KG / 7)
    return -adjustment if profile.goal == Goal.LOSE else adjustment


def _macro_targets(profile: NutritionProfile, calories: int) -> MacroTargets:
    protein_factor = 1.6 if profile.goal != Goal.MAINTAIN else 1.4
    protein_g = round(profile.weight_kg * protein_factor)
    fat_g = round(profile.weight_kg * 0.8)
    protein_kcal = protein_g * 4
    fat_kcal = fat_g * 9
    remaining = max(0, calories - protein_kcal - fat_kcal)
    carbohydrate_g = round(remaining / 4)
    carbohydrate_kcal = carbohydrate_g * 4
    return MacroTargets(
        protein_g=protein_g,
        fat_g=fat_g,
        carbohydrate_g=carbohydrate_g,
        protein_kcal=protein_kcal,
        fat_kcal=fat_kcal,
        carbohydrate_kcal=carbohydrate_kcal,
    )


def _allocate(total: int, count: int) -> list[int]:
    base, remainder = divmod(total, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def calculate_nutrition_plan(profile: NutritionProfile) -> NutritionPlan:
    if profile.age < 18:
        raise UnsupportedProfileError(
            "Version 0.1 supports adults only; nutrition targets for minors require pediatric guidance."
        )

    bmr = calculate_bmr(profile)
    maintenance = round(bmr * ACTIVITY_FACTORS[profile.activity_level])
    requested_adjustment = _goal_adjustment(profile)
    raw_target = maintenance + requested_adjustment
    floor = MIN_CALORIES[profile.sex]
    target = max(raw_target, floor)
    actual_adjustment = target - maintenance

    warnings: list[str] = []
    if target != raw_target:
        warnings.append(
            f"The requested rate was reduced because the calculated target fell below {floor} kcal/day."
        )
    if abs(actual_adjustment) > 750:
        warnings.append("This is an aggressive calorie adjustment; consider professional review.")

    macros = _macro_targets(profile, target)
    meal_calories = _allocate(target, profile.meals_per_day)
    meal_protein = _allocate(macros.protein_g, profile.meals_per_day)
    meal_fat = _allocate(macros.fat_g, profile.meals_per_day)
    meal_carbs = _allocate(macros.carbohydrate_g, profile.meals_per_day)
    meals = [
        MealTarget(
            meal_number=index + 1,
            calories=meal_calories[index],
            protein_g=meal_protein[index],
            fat_g=meal_fat[index],
            carbohydrate_g=meal_carbs[index],
        )
        for index in range(profile.meals_per_day)
    ]

    return NutritionPlan(
        bmr_calories=round(bmr),
        maintenance_calories=maintenance,
        target_calories=target,
        daily_adjustment_calories=actual_adjustment,
        macros=macros,
        meals=meals,
        warnings=warnings,
    )

