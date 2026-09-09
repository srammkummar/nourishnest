"""Member and saved-member nutrition views; HTTP client is the only data boundary."""

from collections.abc import Callable

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from nourish_nest.api_client import APIClient, APIError, Household, Member, MemberInput

SEX = ["female", "male"]
ACTIVITY = ["sedentary", "light", "moderate", "very_active"]
GOALS = ["lose", "maintain", "gain"]
PREFERENCES = ["vegetarian", "vegan", "pescatarian", "halal", "no-beef", "no-pork", "custom"]


def member_form(key: str, member: Member | None = None) -> MemberInput | None:
    defaults = (
        member.model_dump()
        if member
        else {
            "name": "",
            "age": 35,
            "sex": "female",
            "height_cm": 170.0,
            "weight_kg": 70.0,
            "activity_level": "moderate",
            "goal": "maintain",
            "weekly_goal_kg": 0.0,
            "meals_per_day": 3,
            "dietary_preferences": [],
            "allergies": [],
        }
    )
    with st.form(key):
        name = st.text_input(
            "Member name", value=defaults["name"], max_chars=200, key=f"{key}_name"
        )
        a, b = st.columns(2)
        age = a.number_input(
            "Age (years)", min_value=13, max_value=100, value=defaults["age"], key=f"{key}_age"
        )
        sex = b.selectbox(
            "Sex used by the current calculator",
            SEX,
            index=SEX.index(defaults["sex"]),
            key=f"{key}_sex",
        )
        height = a.number_input(
            "Height (cm)",
            min_value=100.0,
            max_value=250.0,
            value=float(defaults["height_cm"]),
            key=f"{key}_height",
        )
        weight = b.number_input(
            "Weight (kg)",
            min_value=30.0,
            max_value=350.0,
            value=float(defaults["weight_kg"]),
            key=f"{key}_weight",
        )
        activity = a.selectbox(
            "Activity level",
            ACTIVITY,
            index=ACTIVITY.index(defaults["activity_level"]),
            format_func=lambda s: s.replace("_", " ").title(),
            key=f"{key}_activity",
        )
        goal = b.selectbox(
            "Goal",
            GOALS,
            index=GOALS.index(defaults["goal"]),
            format_func=lambda s: {
                "lose": "Lose weight",
                "maintain": "Maintain weight",
                "gain": "Gain weight",
            }[s],
            key=f"{key}_goal",
        )
        rate = a.number_input(
            "Weekly weight change (kg)",
            min_value=0.0,
            max_value=1.0,
            value=float(defaults["weekly_goal_kg"]),
            step=0.05,
            help="Use a positive amount for loss or gain. Maintenance saves zero.",
            key=f"{key}_rate",
        )
        meals = b.number_input(
            "Meals per day",
            min_value=2,
            max_value=6,
            value=defaults["meals_per_day"],
            key=f"{key}_meals",
        )
        st.caption("Dietary preferences — add or remove rows. Each row needs a type and value.")
        preferences = st.data_editor(
            pd.DataFrame(
                defaults["dietary_preferences"],
                columns=["preference_type", "value"],
                dtype="string",
            ),
            num_rows="dynamic",
            key=f"{key}_preferences",
            column_config={
                "preference_type": st.column_config.SelectboxColumn(
                    "Preference type", options=PREFERENCES, required=True
                ),
                "value": st.column_config.TextColumn(
                    "Preference / details", required=True, max_chars=200
                ),
            },
        )
        st.caption(
            "Allergies — include severity and optional notes; remove a row to remove an allergy."
        )
        allergies = st.data_editor(
            pd.DataFrame(
                defaults["allergies"], columns=["allergen", "severity", "notes"], dtype="string"
            ),
            num_rows="dynamic",
            key=f"{key}_allergies",
            column_config={
                "allergen": st.column_config.TextColumn("Allergen", required=True, max_chars=200),
                "severity": st.column_config.SelectboxColumn(
                    "Severity", options=["mild", "moderate", "severe"], required=True
                ),
                "notes": st.column_config.TextColumn("Notes", max_chars=2000),
            },
        )
        submitted = st.form_submit_button("Save member" if member else "Add member", type="primary")
    if not submitted:
        return None

    def records(value):
        return value.astype(object).where(value.notna(), None).to_dict("records")

    try:
        return MemberInput(
            name=name,
            age=age,
            sex=sex,
            height_cm=height,
            weight_kg=weight,
            activity_level=activity,
            goal=goal,
            weekly_goal_kg=rate,
            meals_per_day=meals,
            dietary_preferences=records(preferences),
            allergies=records(allergies),
        )
    except ValidationError as error:
        for detail in error.errors():
            field = " / ".join(str(part) for part in detail["loc"]) or "Profile"
            st.error(f"{field}: {detail['msg']}")
        return None


def load_members(api: APIClient, household: Household) -> list[Member]:
    # The collection is the source of selectable IDs; never reuse another household's member.
    return sorted(
        (m for m in api.members(household.id) if m.household_id == household.id),
        key=lambda m: (m.name.casefold(), str(m.id)),
    )


def render_members(
    api: APIClient, household: Household, show_error: Callable[[APIError], None]
) -> None:
    st.subheader("Household members")
    try:
        with st.spinner("Loading members…"):
            members = load_members(api, household)
    except APIError as error:
        show_error(error)
        st.button("Refresh members")
        return
    if st.button("Refresh members"):
        for member in members:
            st.session_state.pop(f"member_snapshot_{member.id}", None)
    mode = st.radio(
        "Member action",
        ["Add member", "Edit member", "Delete member"],
        horizontal=True,
        key=f"member_action_{household.id}",
    )
    selected = None
    if mode != "Add member" and members:
        labels = {str(m.id): m.name for m in members}
        choice = st.selectbox(
            "Member", list(labels), format_func=labels.get, key=f"edit_member_{household.id}"
        )
        selected = next(m for m in members if str(m.id) == choice)
    payload = None
    if mode == "Add member":
        payload = member_form(f"add_{household.id}")
    elif mode == "Edit member" and selected:
        selected = st.session_state.setdefault(f"member_snapshot_{selected.id}", selected)
        payload = member_form(f"edit_{selected.id}_v{selected.version}", selected)
    try:
        if payload:
            with st.spinner("Saving member…"):
                saved = (
                    api.update_member(household.id, selected.id, payload, selected.version)
                    if selected
                    else api.create_member(household.id, payload)
                )
            members = [m for m in members if m.id != saved.id] + [saved]
            st.session_state[f"member_snapshot_{saved.id}"] = saved
            st.success(f"Saved {saved.name}.")
        if mode == "Delete member" and selected:
            st.warning(
                f"Deleting {selected.name} also removes their saved preferences and allergies."
            )
            confirmed = st.checkbox(
                f"I confirm deletion of {selected.name}",
                key=f"confirm_delete_{household.id}_{selected.id}_v{selected.version}",
            )
            if st.button("Delete member", disabled=not confirmed, type="primary") and confirmed:
                with st.spinner("Deleting member…"):
                    api.delete_member(household.id, selected.id, selected.version)
                members = [m for m in members if m.id != selected.id]
                st.success(f"Deleted {selected.name}.")
    except APIError as error:
        show_error(error)
        st.info("The request was not retried. Refresh members before submitting again.")
    if not members:
        st.info("No members yet. Add a saved profile to calculate nutrition.")
    for member in sorted(members, key=lambda m: (m.name.casefold(), str(m.id))):
        with st.container(border=True):
            st.subheader(member.name)
            st.write(f"Age {member.age} · {member.height_cm:g} cm · {member.weight_kg:g} kg")
            st.write(
                f"Calculator sex: {member.sex} · Activity: {member.activity_level.replace('_', ' ')} · Goal: {member.goal}"
            )
            st.write(
                "Preferences: "
                + (", ".join(p.value for p in member.dietary_preferences) or "None recorded")
            )
            st.write(
                "Allergies: "
                + (
                    "; ".join(
                        f"{a.allergen} ({a.severity})" + (f" — {a.notes}" if a.notes else "")
                        for a in member.allergies
                    )
                    or "None recorded"
                )
            )


def render_nutrition(
    api: APIClient, household: Household, show_error: Callable[[APIError], None]
) -> None:
    st.write(
        "Calculate nutrition from a saved member profile. Update the profile on the Household page first if needed."
    )
    st.caption(
        "Results are estimates, not medical advice. Consult a qualified professional for individual dietary needs."
    )
    try:
        with st.spinner("Loading saved members…"):
            members = load_members(api, household)
        if not members:
            st.info("No saved members in this household. Add a member on the Household page first.")
            return
        labels = {str(m.id): m.name for m in members}
        choice = st.selectbox(
            "Saved member",
            list(labels),
            format_func=labels.get,
            key=f"nutrition_member_{household.id}",
        )
        selected = next(m for m in members if str(m.id) == choice)
        if not st.button("Calculate nutrition", type="primary"):
            return
        with st.spinner("Calculating nutrition through the API…"):
            plan = api.member_nutrition(household.id, selected.id)
        st.subheader(f"Nutrition estimate for {selected.name}")
        cards = [
            ("BMR", f"{plan.bmr_calories:,} kcal/day", "Estimated energy your body uses at rest."),
            (
                "Estimated TDEE",
                f"{plan.maintenance_calories:,} kcal/day",
                "Estimated daily energy use including activity; the maintenance target.",
            ),
            (
                "Calorie target",
                f"{plan.target_calories:,} kcal/day",
                "Daily calorie estimate adjusted for the saved goal.",
            ),
            (
                "Protein target",
                f"{plan.macros.protein_g} g/day",
                "Daily protein allocation in the calculated plan.",
            ),
            (
                "Carbohydrate target",
                f"{plan.macros.carbohydrate_g} g/day",
                "Daily carbohydrate allocation in the calculated plan.",
            ),
            (
                "Fat target",
                f"{plan.macros.fat_g} g/day",
                "Daily fat allocation in the calculated plan.",
            ),
        ]
        for start in (0, 3):
            for column, (label, value, explanation) in zip(
                st.columns(3), cards[start : start + 3], strict=True
            ):
                column.metric(label, value)
                column.caption(explanation)
        if plan.calculation_version:
            st.caption(f"Calculation method/version: {plan.calculation_version}")
        for warning in plan.warnings:
            st.warning(warning)
    except APIError as error:
        show_error(error)
        st.info("Refresh the page if this member has been removed or changed.")
