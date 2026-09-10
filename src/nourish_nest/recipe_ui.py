"""Recipe browsing and editing through the typed HTTP client only."""

from collections.abc import Callable
from uuid import UUID, uuid4

import streamlit as st
from pydantic import ValidationError

from nourish_nest.api_client import APIClient, APIError, Household
from nourish_nest.recipe_client_models import RecipeInput, RecipeNutrition, RecipeRecord, StoredFood
from nourish_nest.ui_labels import (
    food_labels,
    humanize,
    unit_label,
    validation_errors,
)
from nourish_nest.ui_labels import (
    labels as human_labels,
)

# Input choices mirror the API's supported units; conversion remains server-side.
UNITS = ("g", "kg", "oz", "lb", "ml", "l", "cup", "tbsp", "tsp", "item")


def new_draft(recipe: RecipeRecord | None = None) -> dict:
    values = (
        recipe.model_dump(mode="json")
        if recipe
        else {
            "name": "",
            "description": "",
            "cuisine": "",
            "preparation_minutes": 0,
            "cooking_minutes": 0,
            "servings": "2",
            "source": "",
            "ingredients": [],
            "instructions": [],
        }
    )
    return {
        "token": uuid4().hex,
        "idempotency_key": uuid4().hex if recipe is None else None,
        "expected_version": recipe.version if recipe else None,
        "recipe_id": recipe.id if recipe else None,
        "values": values,
        "foods": [],
        "ingredients": [
            {**row, "row_id": uuid4().hex}
            for row in sorted(values["ingredients"], key=lambda r: r["display_order"])
        ],
        "instructions": [
            {**row, "row_id": uuid4().hex}
            for row in sorted(values["instructions"], key=lambda r: r["step_number"])
        ],
    }


def move_row(rows: list[dict], index: int, offset: int) -> None:
    destination = index + offset
    if 0 <= destination < len(rows):
        rows[index], rows[destination] = rows[destination], rows[index]


def add_ingredient(draft: dict, food: StoredFood) -> None:
    draft["ingredients"].append(
        {
            "row_id": uuid4().hex,
            "food_id": str(food.id),
            "food": food.model_dump(mode="json"),
            "quantity": "1",
            "unit": "g",
            "preparation_note": "",
        }
    )


def build_payload(draft: dict) -> RecipeInput:
    values = {
        key: value
        for key, value in draft["values"].items()
        if key in RecipeInput.model_fields and key not in {"ingredients", "instructions"}
    }
    return RecipeInput(
        **values,
        ingredients=[
            {
                **{k: row.get(k) for k in ("food_id", "quantity", "unit", "preparation_note")},
                "display_order": i,
            }
            for i, row in enumerate(draft["ingredients"])
        ],
        instructions=[
            {"step_number": i + 1, "instruction": row["instruction"]}
            for i, row in enumerate(draft["instructions"])
        ],
    )


def finish_save(workspace: dict, saved: RecipeRecord) -> None:
    workspace["recipes"] = [r for r in workspace["recipes"] if r.id != saved.id] + [saved]
    workspace["selected"] = str(saved.id)
    workspace["detail"] = saved
    workspace["nutrition"] = None
    workspace["draft"] = None
    workspace["notice"] = f"Saved {saved.name}."
    workspace["reset_filters"] = True


def nutrition_rows(result: RecipeNutrition) -> list[dict[str, str]]:
    def shown(value, unit):
        return "Not available" if value is None else f"{value:f} {unit}"

    rows = [
        {
            "Nutrient": "Calories",
            "Recipe total": shown(result.total_calories, "kcal"),
            "Per serving": shown(result.calories_per_serving, "kcal"),
        }
    ]
    for field, label in (
        ("protein_g", "Protein"),
        ("carbohydrate_g", "Carbohydrate"),
        ("fat_g", "Fat"),
        ("fiber_g", "Fiber"),
        ("sugar_g", "Sugar"),
        ("sodium_mg", "Sodium"),
    ):
        unit = "mg" if field == "sodium_mg" else "g"
        rows.append(
            {
                "Nutrient": label,
                "Recipe total": shown(getattr(result.total_macros, field), unit),
                "Per serving": shown(getattr(result.macros_per_serving, field), unit),
            }
        )
    return rows


def row_controls(rows: list[dict], index: int, key: str) -> None:
    a, b, c = st.columns(3)
    if a.button("Move up", key=f"{key}_up", disabled=index == 0):
        move_row(rows, index, -1)
        st.rerun()
    if b.button("Move down", key=f"{key}_down", disabled=index == len(rows) - 1):
        move_row(rows, index, 1)
        st.rerun()
    if c.button("Remove", key=f"{key}_remove"):
        rows.pop(index)
        st.rerun()


def editor(api: APIClient, household: Household, workspace: dict, show_error: Callable) -> None:
    draft = workspace["draft"]
    token, values = draft["token"], draft["values"]
    st.caption(
        "Name, servings, and at least one ingredient are required. Add ingredients in cooking order; you can move or remove rows before saving."
    )
    st.subheader("Edit household recipe" if draft["recipe_id"] else "Create household recipe")
    if st.button("Cancel editing"):
        workspace["draft"] = None
        st.rerun()
    values["name"] = st.text_input(
        "Recipe name", values["name"], max_chars=200, key=f"{token}_name"
    )
    values["description"] = st.text_area(
        "Description (optional)",
        values.get("description") or "",
        max_chars=4000,
        key=f"{token}_description",
    )
    a, b = st.columns(2)
    values["cuisine"] = a.text_input(
        "Cuisine (optional)", values.get("cuisine") or "", max_chars=100, key=f"{token}_cuisine"
    )
    values["servings"] = b.text_input(
        "Servings",
        str(values["servings"]),
        help="Positive number, up to two decimal places.",
        key=f"{token}_servings",
    )
    values["preparation_minutes"] = a.number_input(
        "Preparation time (minutes)",
        min_value=0,
        max_value=10000,
        value=values["preparation_minutes"],
        key=f"{token}_prep",
    )
    values["cooking_minutes"] = b.number_input(
        "Cooking time (minutes)",
        min_value=0,
        max_value=10000,
        value=values["cooking_minutes"],
        key=f"{token}_cook",
    )
    values["source"] = st.text_input(
        "Source (optional)", values.get("source") or "", max_chars=200, key=f"{token}_source"
    )
    st.subheader("Ingredients")
    with st.form(f"{token}_search"):
        query = st.text_input(
            "Search stored foods",
            help="Search NourishNest's existing food catalog. No external import.",
        )
        search = st.form_submit_button("Search foods")
    if search:
        try:
            with st.spinner("Searching stored foods…"):
                draft["foods"] = api.search_foods(query.strip())
            if not draft["foods"]:
                st.info(
                    "No stored foods match. Try another name; foods must already exist in NourishNest."
                )
        except APIError as error:
            show_error(error)
    if draft["foods"]:
        labels = {str(key): value for key, value in food_labels(draft["foods"]).items()}
        food_id = st.selectbox(
            "Stored food", list(labels), format_func=labels.get, key=f"{token}_food"
        )
        if st.button("Add ingredient"):
            add_ingredient(draft, next(f for f in draft["foods"] if str(f.id) == food_id))
    if not draft["ingredients"]:
        st.info("Search for a stored food and add at least one ingredient.")
    for i, row in enumerate(draft["ingredients"]):
        key = f"{token}_{row['row_id']}"
        with st.container(border=True):
            st.write(f"Ingredient {i + 1}: {row['food']['name']}")
            a, b = st.columns(2)
            row["quantity"] = a.text_input(
                "Quantity",
                str(row["quantity"]),
                help="Positive quantity, up to three decimal places.",
                key=f"{key}_quantity",
            )
            units = list(UNITS)
            if row["unit"] not in units:
                units.append(row["unit"])
                st.caption("Existing unit preserved; nutrition may report conversion warnings.")
            row["unit"] = b.selectbox(
                "Unit",
                units,
                format_func=unit_label,
                index=units.index(row["unit"]),
                key=f"{key}_unit",
            )
            row["preparation_note"] = st.text_input(
                "Preparation note (optional)",
                row.get("preparation_note") or "",
                max_chars=300,
                key=f"{key}_note",
            )
            row_controls(draft["ingredients"], i, key)
    st.subheader("Cooking instructions")
    st.caption("Step numbers follow the displayed order automatically.")
    if st.button("Add instruction"):
        draft["instructions"].append({"row_id": uuid4().hex, "instruction": ""})
    for i, row in enumerate(draft["instructions"]):
        key = f"{token}_{row['row_id']}"
        row["instruction"] = st.text_area(
            f"Step {i + 1}", row["instruction"], max_chars=4000, key=f"{key}_instruction"
        )
        row_controls(draft["instructions"], i, key)
    if st.button("Save recipe", type="primary"):
        try:
            payload = build_payload(draft)
            with st.spinner("Saving recipe…"):
                saved = (
                    api.update_recipe(
                        household.id, draft["recipe_id"], payload, draft["expected_version"]
                    )
                    if draft["recipe_id"]
                    else api.create_recipe(household.id, payload, draft["idempotency_key"])
                )
            finish_save(workspace, saved)
            st.rerun()
        except ValidationError as error:
            validation_errors(error)
        except APIError as error:
            show_error(error)
            st.info(
                "The save was not automatically retried. Retry creation with unchanged fields to reuse its creation key. Cancel editing explicitly discards the draft and its key. For a stale update, cancel and refresh before editing again."
            )
            if error.code == "stale_recipe_version":
                st.caption(
                    "Refresh recipe discards these unsaved edits and loads the latest saved recipe."
                )
                if st.button("Refresh recipe"):
                    workspace.update(draft=None, recipes=None, detail=None, nutrition=None)
                    st.rerun()


def details(
    api: APIClient,
    household: Household,
    workspace: dict,
    recipe: RecipeRecord,
    show_error: Callable,
) -> None:
    st.subheader(recipe.name)
    system = recipe.household_id is None
    st.caption("System recipe" if system else "Household recipe")
    st.write(recipe.description or "No description provided.")
    st.write(
        f"Cuisine: {recipe.cuisine or 'Not specified'} · Preparation: {recipe.preparation_minutes} min · Cooking: {recipe.cooking_minutes} min · Servings: {recipe.servings}"
    )
    if recipe.source:
        st.caption(f"Source: {humanize(recipe.source)}")
    st.subheader("Ingredients")
    for row in sorted(recipe.ingredients, key=lambda r: r.display_order):
        st.write(
            f"• {row.food.name}: {row.quantity} {row.unit}"
            + (f" — {row.preparation_note}" if row.preparation_note else "")
        )
    st.subheader("Cooking instructions")
    for row in sorted(recipe.instructions, key=lambda r: r.step_number):
        st.write(f"{row.step_number}. {row.instruction}")
    if not recipe.instructions:
        st.info("No instructions recorded.")
    st.subheader("Nutrition")
    try:
        if workspace["nutrition"] is None:
            with st.spinner("Loading recipe nutrition…"):
                workspace["nutrition"] = api.recipe_nutrition(household.id, recipe.id)
        result = workspace["nutrition"]
        st.dataframe(nutrition_rows(result), hide_index=True, use_container_width=True)
        st.write(
            "Allergens: "
            + (
                ", ".join(humanize(value) for value in result.aggregated_allergens)
                or "None reported by stored food data"
            )
        )
        st.write(
            "Dietary tags: "
            + (", ".join(humanize(value) for value in result.dietary_tags) or "None reported")
        )
        for warning in result.warnings:
            st.warning(warning)
        st.caption(f"Calculation version: {result.calculation_version}")
        st.caption(
            "Nutrition uses stored food data. Review warnings for missing or incomplete values."
        )
    except APIError as error:
        show_error(error)
        st.info("Nutrition, allergens, and tags could not be loaded. Refresh recipes to retry.")
    if system:
        st.info(
            "Shared system recipes are read-only. Create a household recipe to maintain your own version."
        )
        return
    if st.button("Edit recipe"):
        workspace["draft"] = new_draft(recipe)
        st.rerun()
    with st.expander("Delete household recipe"):
        confirmed = st.checkbox(
            f"I confirm deletion of {recipe.name}", key=f"delete_recipe_{household.id}_{recipe.id}"
        )
        if st.button("Delete recipe", disabled=not confirmed) and confirmed:
            try:
                api.delete_recipe(household.id, recipe.id, recipe.version)
                workspace["recipes"] = [r for r in workspace["recipes"] if r.id != recipe.id]
                workspace["selected"] = None
                workspace["detail"] = workspace["nutrition"] = None
                workspace["notice"] = f"Deleted {recipe.name}."
                st.rerun()
            except APIError as error:
                show_error(error)


def render_recipes(api: APIClient, household: Household, show_error: Callable) -> None:
    st.write(
        "Choose a recipe to see ingredients and nutrition, or create your own household recipe."
    )
    workspace = st.session_state.setdefault(
        f"recipes_{household.id}",
        {
            "recipes": None,
            "selected": None,
            "detail": None,
            "nutrition": None,
            "draft": None,
            "notice": None,
        },
    )
    if workspace["notice"]:
        st.success(workspace.pop("notice"))
        workspace["notice"] = None
    try:
        if workspace["recipes"] is None:
            with st.spinner("Loading recipes…"):
                workspace["recipes"] = [
                    r for r in api.recipes(household.id) if r.household_id in (None, household.id)
                ]
        if workspace["draft"] is not None:
            editor(api, household, workspace, show_error)
            return
        a, b = st.columns(2)
        if a.button("Create recipe", type="primary"):
            workspace["draft"] = new_draft()
            st.rerun()
        if b.button("Refresh recipes"):
            workspace["recipes"] = workspace["detail"] = workspace["nutrition"] = None
            st.rerun()
        if workspace.pop("reset_filters", False):
            st.session_state[f"recipe_search_{household.id}"] = ""
            st.session_state[f"recipe_cuisine_{household.id}"] = "All cuisines"
        query = st.text_input("Search recipe name", key=f"recipe_search_{household.id}")
        cuisines = sorted({r.cuisine for r in workspace["recipes"] if r.cuisine})
        cuisine = (
            st.selectbox(
                "Cuisine filter", ["All cuisines", *cuisines], key=f"recipe_cuisine_{household.id}"
            )
            if cuisines
            else "All cuisines"
        )
        recipes = sorted(
            (
                r
                for r in workspace["recipes"]
                if query.casefold() in r.name.casefold()
                and (cuisine == "All cuisines" or r.cuisine == cuisine)
            ),
            key=lambda r: (r.name.casefold(), str(r.id)),
        )
        if not recipes:
            st.info(
                "No recipes match. Clear filters or create a household recipe."
                if workspace["recipes"]
                else "No recipes yet. Create your first household recipe."
            )
            return
        labels = {
            str(key): value
            for key, value in human_labels(
                recipes,
                context=lambda r: "System recipe" if r.household_id is None else "Household recipe",
            ).items()
        }
        selected = workspace["selected"] if workspace["selected"] in labels else next(iter(labels))
        widget_key = f"selected_recipe_{household.id}"
        # Pending selection after save is applied before the selector is instantiated.
        if (
            workspace.get("selector_options") != list(labels)
            or st.session_state.get(widget_key) not in labels
        ):
            st.session_state[widget_key] = selected
        choice = st.selectbox("Recipe", list(labels), format_func=labels.get, key=widget_key)
        workspace["selector_options"] = list(labels)
        workspace["selected"] = choice
        if workspace["detail"] is None or str(workspace["detail"].id) != choice:
            workspace["detail"] = api.get_recipe(household.id, UUID(choice))
            workspace["nutrition"] = None
        recipe = workspace["detail"]
        if recipe.household_id not in (None, household.id):
            st.error("This recipe is not available in the selected household.")
            return
        details(api, household, workspace, recipe, show_error)
    except APIError as error:
        show_error(error)
        if st.button("Retry loading recipes"):
            workspace["recipes"] = workspace["detail"] = workspace["nutrition"] = None
            st.rerun()
