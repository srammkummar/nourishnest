"""Session-local planning with HTTP-only data and server-provided nutrition."""

from decimal import Decimal
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from nourish_nest.api_client import APIError
from nourish_nest.grocery_client_models import GenerationInput, RecipeSelection, RequirementsInput
from nourish_nest.grocery_ui import item_rows, render_preview, render_warnings
from nourish_nest.planning_contracts import RecommendationRequest
from nourish_nest.ui_design import badge, illustration, recipe_image, week_cards
from nourish_nest.ui_labels import friendly_message, labels, technical_details, validation_errors

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SLOTS = ("Breakfast", "Lunch", "Dinner", "Snack (optional)")
NUTRIENTS = ("calories", "protein_g", "carbohydrate_g", "fat_g")


def new_plan():
    return {
        "meals": {},
        "recommendations": None,
        "previews": None,
        "pending": None,
        "generation": None,
        "error": None,
    }


def consolidate(meals):
    quantities = {}
    for meal in meals.values():
        recipe_id = meal["recipe"].recipe_id
        quantities[recipe_id] = quantities.get(recipe_id, Decimal(0)) + meal["servings"]
    return RequirementsInput(
        recipes=[
            RecipeSelection(recipe_id=key, desired_servings=value)
            for key, value in sorted(quantities.items())
        ]
    )


def nutrition_summary(meals):
    # Only scale and sum server-supplied values; no nutrition formula or unit conversion.
    days = {day: dict.fromkeys(NUTRIENTS, Decimal(0)) for day in DAYS}
    incomplete = set()
    for (day, _), meal in meals.items():
        nutrition = meal["recipe"].nutrition_per_serving
        if nutrition.warnings:
            incomplete.add(day)
        for field in NUTRIENTS:
            value = getattr(nutrition, field)
            if value is None:
                days[day][field] = None
                incomplete.add(day)
            elif days[day][field] is not None:
                days[day][field] += value * meal["servings"]
    weekly = {
        field: None
        if any(day[field] is None for day in days.values())
        else sum((day[field] for day in days.values()), Decimal(0))
        for field in NUTRIENTS
    }
    return days, weekly, incomplete


def need_rows(needs):
    return [
        {
            "Food": n.food_name,
            "Amount to buy": f"{n.missing_quantity} {n.unit}",
            "Note": "" if n.conversion_supported else "Conversion unavailable; check amount",
        }
        for n in needs
    ]


def render_recommendations(api, home, member, workspace, prefix):
    st.subheader("Find meals for your pantry")
    st.caption(
        "Recommendations use the recipe’s saved yield. Your planned servings can be changed below."
    )
    with st.form(f"{prefix}_filters"):
        cuisine = st.text_input("Cuisine (optional)")
        maximum_time = st.number_input(
            "Maximum cooking time (minutes, optional)", min_value=1, max_value=1440, value=None
        )
        missing = st.number_input(
            "Acceptable missing ingredients", min_value=0, max_value=100, value=3
        )
        submitted = st.form_submit_button("Find recipes", type="primary")
    if submitted:
        workspace.pop("focus_recipe", None)
        with st.spinner("Checking pantry and recipe details…"):
            workspace["recommendations"] = api.recipe_recommendations(
                home,
                RecommendationRequest(
                    member_id=member.id if member else None,
                    cuisine=cuisine.strip() or None,
                    maximum_cooking_minutes=maximum_time,
                    maximum_missing_ingredients=missing,
                ),
            )
    response = workspace["recommendations"]
    if response is None:
        st.info("Choose your filters, then find recipes to start planning.")
        return
    for warning in response.warnings:
        st.info(friendly_message(warning.message))
    technical_details(
        calculation_version=response.calculation_version,
        calculation_as_of=response.calculation_as_of,
    )
    if not response.recommendations:
        st.info(
            "No recipes meet these filters and stored dietary requirements. Try different filters or review food data."
        )
    selected_recipes = [r for r in response.recommendations if workspace.get("focus_recipe") in (None, r.recipe_id)]
    if workspace.get("focus_recipe") and not selected_recipes:
        st.info("That recipe was not returned in the eligible pantry matches. Find recipes to explore available choices; dietary filters remain in effect.")
    for recipe in selected_recipes:
        with st.container(border=True):
            illustration(recipe_image(recipe.cuisine))
            st.subheader(recipe.recipe_name)
            st.caption("System recipe" if recipe.system_recipe else "Household recipe")
            st.write(
                f"**{recipe.classification}** · Pantry coverage {recipe.coverage_percentage:.1f}%"
            )
            st.write(recipe.explanation)
            st.caption(
                f"Preparation {recipe.preparation_minutes} min · Cooking {recipe.cooking_minutes} min · Makes {recipe.servings} servings"
            )
            calories = recipe.nutrition_per_serving.calories
            st.write(f"Calories per serving: {calories if calories is not None else 'Unavailable'}")
            if recipe.missing_ingredients:
                st.dataframe(need_rows(recipe.missing_ingredients), hide_index=True)
            if recipe.expiring_ingredients:
                badge("Uses expiring ingredients", "success")
                st.write(
                    "Use soon: "
                    + "; ".join(
                        f"{food.food_name} — {food.quantity} {food.unit}, expires {food.earliest_expiration}"
                        for food in recipe.expiring_ingredients
                    )
                )
            for warning in recipe.warnings:
                st.warning(friendly_message(warning.message))
            for warning in recipe.nutrition_per_serving.warnings:
                st.warning(friendly_message(warning))
            with st.expander("Why this recipe?"):
                st.write(
                    f"Quantity coverage: {recipe.score.coverage_points:.2f}/80 points. Expiring ingredients: {recipe.score.expiring_points:.2f}/20 points. Total: {recipe.score.total:.2f}/100."
                )
                st.caption(
                    "Each food/unit requirement contributes its covered fraction. Different units are never added together."
                )
            technical_details(recipe_ID=recipe.recipe_id)
            token = f"{prefix}_{recipe.recipe_id}"
            with st.form(f"{token}_add"):
                day = st.selectbox("Day", DAYS, key=f"{token}_day")
                slot = st.selectbox("Meal", SLOTS, key=f"{token}_slot")
                servings = st.text_input("Servings to plan", "1", key=f"{token}_servings")
                replace = st.checkbox(
                    "Replace this meal if already planned", key=f"{token}_replace"
                )
                add = st.form_submit_button("Add to weekly plan")
            if add:
                value = RecipeSelection(
                    recipe_id=recipe.recipe_id, desired_servings=servings
                ).desired_servings
                if (day, slot) in workspace["meals"] and not replace:
                    st.error(
                        "That meal is already planned. Choose another slot or confirm replacement."
                    )
                else:
                    workspace["meals"][(day, slot)] = {"recipe": recipe, "servings": value}
                    workspace["previews"] = None
                    st.success(f"Planned {recipe.recipe_name} for {day} {slot.lower()}.")


def render_week(api, home, member, workspace, prefix):
    st.subheader("Your seven-day plan")
    st.caption(
        "Totals include every entered serving. For a member comparison, plan only the portions that member will eat. Missing meals are not a complete daily diet."
    )
    for day in DAYS:
        with st.expander(day, expanded=False):
            for slot in SLOTS:
                meal = workspace["meals"].get((day, slot))
                if not meal:
                    st.write(f"{slot}: Not planned")
                    continue
                st.write(f"**{slot}: {meal['recipe'].recipe_name}**")
                with st.form(f"{prefix}_{day}_{slot}_edit"):
                    servings = st.text_input("Planned servings", str(meal["servings"]))
                    save = st.form_submit_button("Update servings")
                    remove = st.form_submit_button("Remove meal")
                if save:
                    meal["servings"] = RecipeSelection(
                        recipe_id=meal["recipe"].recipe_id, desired_servings=servings
                    ).desired_servings
                    workspace["previews"] = None
                    st.success(f"Updated servings for {meal['recipe'].recipe_name}.")
                if remove:
                    del workspace["meals"][(day, slot)]
                    workspace["previews"] = None
                    st.rerun()
    if not workspace["meals"]:
        st.info("Your weekly plan is empty. Add a recommendation to any meal slot.")
        return
    days, weekly, incomplete = nutrition_summary(workspace["meals"])

    def row(label, values):
        return {
            "Period": label,
            **{
                name.replace("_g", " (g)").capitalize(): "Unavailable"
                if value is None
                else str(value.quantize(Decimal("0.01")))
                for name, value in values.items()
            },
        }

    st.dataframe(
        [row(day, values) for day, values in days.items()] + [row("Week total", weekly)],
        hide_index=True,
    )
    if incomplete:
        st.warning(
            "Nutrition is incomplete for: "
            + ", ".join(day for day in DAYS if day in incomplete)
            + ". Missing values are not a complete estimate."
        )
    st.caption(
        "Estimates, not medical advice. Nutrition is supplied by the server; quantities are scaled by planned servings."
    )
    if member and member.age < 18:
        st.info("Adult calorie targets are not applied to members under 18.")
    elif member and st.button("Compare with member target", key=f"{prefix}_target"):
        target = api.member_nutrition(home, member.id)
        target_values = {
            "calories": Decimal(str(target.target_calories)),
            "protein_g": Decimal(str(target.macros.protein_g)),
            "carbohydrate_g": Decimal(str(target.macros.carbohydrate_g)),
            "fat_g": Decimal(str(target.macros.fat_g)),
        }
        st.write(f"Daily targets for {member.name}")
        st.dataframe(
            [
                row("Daily target", target_values),
                row("Seven-day target", {k: v * 7 for k, v in target_values.items()}),
            ],
            hide_index=True,
        )
        st.dataframe(
            [
                row(
                    f"{day} minus target",
                    {
                        k: None if values[k] is None else values[k] - target_values[k]
                        for k in NUTRIENTS
                    },
                )
                for day, values in days.items()
            ]
            + [
                row(
                    "Week minus target",
                    {
                        k: None if weekly[k] is None else weekly[k] - target_values[k] * 7
                        for k in NUTRIENTS
                    },
                )
            ],
            hide_index=True,
        )
        for warning in target.warnings:
            st.warning(warning)
    with st.expander("Reset weekly plan"):
        confirm = st.checkbox("Clear all seven days", key=f"{prefix}_clear")
        if st.button("Reset plan", disabled=not confirm, key=f"{prefix}_reset"):
            workspace.update(meals={}, previews=None)
            st.rerun()


def save_generation(api, home, workspace):
    pending = workspace["pending"]
    try:
        workspace["generation"] = api.generate_grocery_list(
            home, pending["list_id"], pending["data"]
        )
    except APIError as error:
        workspace["error"] = error
        # Render the retained-request controls immediately; this rerun never retries a write.
        st.rerun()
    workspace["pending"] = None
    st.session_state.pop(f"groceries_{home}", None)
    st.rerun()


def grocery_handoff(api, home, workspace, prefix):
    st.subheader("Prepare grocery needs")
    st.caption(
        "Availability is a point-in-time estimate. Planning does not consume or reserve pantry stock. The grocery preview combines all planned portions."
    )
    if workspace["pending"]:
        st.info(
            "A grocery request is pending. Retry sends the saved plan and version unchanged, even if you have edited this plan since then."
        )
        if st.button("Retry grocery generation", key=f"{prefix}_retry"):
            save_generation(api, home, workspace)
        if st.button("Reset grocery request", key=f"{prefix}_reset_request"):
            workspace["pending"] = None
            st.rerun()
    if st.button("Prepare grocery needs", disabled=not workspace["meals"], key=f"{prefix}_prepare"):
        selections = consolidate(workspace["meals"])
        requirements = api.grocery_requirements(home, selections)
        shortages = api.grocery_shortages(home, selections)
        workspace["previews"] = (requirements, shortages, selections)
    if workspace["previews"]:
        requirements, shortages, selections = workspace["previews"]
        with st.expander("All recipe requirements"):
            render_preview(requirements)
        render_preview(shortages)
        listings = [r for r in api.grocery_lists(home) if r.status in ("draft", "active")]
        if not listings:
            st.info("Create a draft or active list on Grocery Lists to save these needs.")
        else:
            choices = labels(listings, context=lambda r: r.status.capitalize())
            selected = st.selectbox(
                "Save to grocery list", list(choices), format_func=choices.get, key=f"{prefix}_list"
            )
            listing = next(r for r in listings if r.id == selected)
            st.caption(
                "One recipe generation is allowed per list. Saving recalculates pantry shortages and keeps existing manual items."
            )
            if st.button(
                "Generate grocery list",
                type="primary",
                disabled=workspace["pending"] is not None,
                key=f"{prefix}_generate",
            ):
                workspace["pending"] = {
                    "list_id": listing.id,
                    "data": GenerationInput(
                        recipes=selections.recipes,
                        expected_list_version=listing.version,
                        idempotency_key=uuid4().hex,
                    ),
                }
                save_generation(api, home, workspace)
    result = workspace["generation"]
    if result:
        st.success(
            "Grocery generation saved."
            if not result.replayed
            else "Existing grocery generation recovered without duplicates."
        )
        if result.created_items:
            st.dataframe(item_rows(result.created_items), hide_index=True)
        else:
            st.info("No items needed purchasing; an empty generation was saved.")
        render_warnings(result.warnings)
        technical_details(generation_ID=result.generation_run_id)


def render_planner(api, household, show_error):
    st.write("Find meals that use your pantry, arrange a week, then prepare a shopping list.")
    st.info(
        "This plan is saved only in this Streamlit session and disappears when the session ends."
    )
    try:
        members = [m for m in api.members(household.id) if m.household_id == household.id]
        choices = labels(members, context=lambda m: f"Age {m.age}")
        selection = st.selectbox(
            "Plan for",
            [None, *choices],
            format_func=lambda key: "Whole household" if key is None else choices[key],
            key=f"planner_member_{household.id}",
        )
        st.session_state[f"planner_profile_{household.id}"] = selection
        member = next((m for m in members if m.id == selection), None)
        prefix = f"planner_{household.id}_{selection}"
        workspace = st.session_state.setdefault(prefix, new_plan())
        requested = st.session_state.pop("planner_recipe", None)
        if requested and requested["household_id"] == household.id:
            workspace["focus_recipe"] = requested["recipe_id"]
            workspace["recommendations"] = api.recipe_recommendations(
                household.id, RecommendationRequest(member_id=selection, maximum_missing_ingredients=100, limit=50)
            )
        overview = st.container()
        if workspace["error"]:
            show_error(workspace["error"])
            workspace["error"] = None
        render_recommendations(api, household.id, member, workspace, prefix)
        with overview:
            st.subheader("This week at your table")
            week_cards(workspace["meals"], DAYS)
        render_week(api, household.id, member, workspace, prefix)
        grocery_handoff(api, household.id, workspace, prefix)
    except ValidationError as error:
        validation_errors(error)
    except APIError as error:
        show_error(error)
        st.info(
            "Your plan and any pending grocery request have been kept. Refresh before retrying a stale request; reset it only to start a different request."
        )
        st.button("Refresh planner", key=f"planner_refresh_{household.id}")
