"""Home-screen composition from existing HTTP snapshots and household-local UI state."""

import streamlit as st

from nourish_nest.api_client import APIError
from nourish_nest.planning_contracts import RecommendationRequest
from nourish_nest.ui_design import badge, illustration, recipe_image, week_cards
from nourish_nest.ui_state import navigate

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def planner_snapshot(home):
    member = st.session_state.get(f"planner_profile_{home}", st.session_state.get(f"planner_member_{home}"))
    return st.session_state.get(f"planner_{home}_{member}", {})


def render_dashboard(api, household, show_error, dashboard_cards, quick_actions):
    home = household.id
    for column, (label, page) in zip(st.columns(3), (
        ("Plan meals", "Meal Planner"), ("Ask NourishNest", "AI Assistant"),
        ("Review grocery list", "Grocery Lists"),
    ), strict=True):
        column.button(label, type="primary" if page == "Meal Planner" else "secondary",
                      on_click=navigate, args=(st.session_state, page), use_container_width=True)
    workspace = st.session_state.setdefault(f"home_overview_{home}", {"counts": None, "recommendations": None})
    if st.button("Refresh dashboard"):
        try:
            with st.spinner("Refreshing your household overview…"):
                workspace["counts"] = api.dashboard(home)
        except APIError as error:
            show_error(error)
            st.info("The overview could not be loaded. Refresh to try again.")
    st.caption("Refresh checks pantry freshness and updates overdue stock to expired. Other browsing leaves inventory unchanged.")
    counts = workspace["counts"]
    plan = planner_snapshot(home)
    meals = plan.get("meals", {})
    grocery = st.session_state.get(f"groceries_{home}", {})
    record = grocery.get("records", {}).get(str(grocery.get("selected")), {})
    items = record.get("items")
    progress = f"{sum(item.checked for item in items)} / {len(items)}" if items is not None else "—"
    for column, (label, value) in zip(st.columns(4), (
        ("Pantry items", counts.active_pantry_items if counts else "—"),
        ("Expiring soon", counts.expiring_items if counts else "—"),
        ("Planned meals", len(meals)), ("Grocery progress", progress),
    ), strict=True):
        column.metric(label, value)
    st.caption("Overview uses your last refresh. Plan and grocery progress reflect the current session and selected list.")
    with st.expander("More household details"):
        if counts:
            dashboard_cards(counts)
        else:
            st.info("Refresh dashboard to load household totals. No dashboard data has been loaded yet.")
            for label, page, intent in (
                ("View expiring items", "Pantry", "Expiring items"),
                ("View low-stock items", "Pantry", "Low-stock items"),
                ("View active grocery lists", "Grocery Lists", "Active grocery lists"),
            ):
                st.button(label, on_click=navigate, args=(st.session_state, page, intent))
    st.subheader("Use these soon")
    pantry = st.session_state.get(f"pantry_{home}", {})
    snapshot = pantry.get("snapshot")
    if snapshot and snapshot["expiring"]:
        for lot in snapshot["expiring"][:4]:
            with st.container(border=True):
                st.write(f"**{pantry['foods'][lot.food_id].name}** · {lot.quantity} {lot.unit}")
                badge(f"Use soon · {lot.expiration_date}", "warning")
    else:
        st.caption("Open Pantry and refresh freshness to see ingredients worth using next.")
    st.subheader("A little inspiration for your table")
    if st.button("Find meal ideas"):
        try:
            workspace["recommendations"] = api.recipe_recommendations(home, RecommendationRequest(limit=3))
        except APIError as error:
            show_error(error)
    recommendations = workspace["recommendations"] or plan.get("recommendations")
    if recommendations:
        for column, recipe in zip(st.columns(3), recommendations.recommendations[:3], strict=False):
            with column.container(border=True):
                illustration(recipe_image(recipe.cuisine))
                st.subheader(recipe.recipe_name)
                badge(recipe.classification)
                st.caption(f"{recipe.cooking_minutes} min cooking · {recipe.coverage_percentage}% pantry match")
                st.write(recipe.explanation)
                for warning in recipe.warnings:
                    st.warning(warning.message)
        for warning in recommendations.warnings:
            st.warning(warning.message)
        if not recommendations.recommendations:
            st.info("No recipe matches yet. Add recipes or try different filters in Meal Planner.")
    else:
        left, right = st.columns([1, 2])
        with left:
            illustration("recipe")
        with right:
            st.write("**Good meals can start with what is already at home.**")
            st.caption("Find meal ideas to check your household pantry. For personal allergies or dietary preferences, choose a member in Meal Planner.")
    st.subheader("Your week, at a glance")
    st.caption("Session-local plan · selected planning profile · no stock reserved")
    week_cards(meals, DAYS)
    with st.expander("More ways to get started"):
        quick_actions()
