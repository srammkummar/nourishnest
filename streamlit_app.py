import streamlit as st

from nourish_nest.domain import ActivityLevel, Goal, NutritionProfile, Sex
from nourish_nest.nutrition import UnsupportedProfileError, calculate_nutrition_plan

st.set_page_config(page_title="NourishNest", page_icon="🏠", layout="wide")
st.title("NourishNest")
st.caption("Nutrition planning foundation — calculations are deterministic and reviewable")

with st.form("nutrition-profile"):
    left, middle, right = st.columns(3)
    with left:
        age = st.number_input("Age", min_value=13, max_value=100, value=35)
        sex = st.selectbox("Sex used by calorie formula", [item.value for item in Sex])
        height_cm = st.number_input("Height (cm)", min_value=100.0, max_value=250.0, value=170.0)
    with middle:
        weight_kg = st.number_input("Weight (kg)", min_value=30.0, max_value=350.0, value=75.0)
        activity = st.selectbox("Activity level", [item.value for item in ActivityLevel], index=2)
        goal = st.selectbox("Goal", [item.value for item in Goal], index=1)
    with right:
        default_rate = 0.0 if goal == Goal.MAINTAIN.value else 0.25
        weekly_goal = st.number_input(
            "Weekly change goal (kg)", min_value=0.0, max_value=1.0, value=default_rate, step=0.05
        )
        meals_per_day = st.slider("Meals per day", 2, 6, 3)
    submitted = st.form_submit_button("Calculate nutrition plan", type="primary")

if submitted:
    try:
        profile = NutritionProfile(
            age=age,
            sex=sex,
            height_cm=height_cm,
            weight_kg=weight_kg,
            activity_level=activity,
            goal=goal,
            weekly_goal_kg=weekly_goal,
            meals_per_day=meals_per_day,
        )
        plan = calculate_nutrition_plan(profile)
        a, b, c = st.columns(3)
        a.metric("Basal metabolic rate", f"{plan.bmr_calories:,} kcal")
        b.metric("Estimated maintenance", f"{plan.maintenance_calories:,} kcal")
        c.metric("Daily target", f"{plan.target_calories:,} kcal")

        st.subheader("Daily macro targets")
        st.write(
            {
                "Protein": f"{plan.macros.protein_g} g",
                "Fat": f"{plan.macros.fat_g} g",
                "Carbohydrate": f"{plan.macros.carbohydrate_g} g",
            }
        )
        st.subheader("Per-meal allocation")
        st.dataframe([meal.model_dump() for meal in plan.meals], use_container_width=True)
        for warning in plan.warnings:
            st.warning(warning)
        st.info("Educational estimate only. Medical conditions and therapeutic diets need professional guidance.")
    except (ValueError, UnsupportedProfileError) as exc:
        st.error(str(exc))

