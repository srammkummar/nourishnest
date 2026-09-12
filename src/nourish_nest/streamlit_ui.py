"""Streamlit presentation layer; all application data arrives through HTTP."""

import streamlit as st

from nourish_nest.api_client import (
    APIClient,
    APIError,
    DashboardCounts,
    Household,
    UISettings,
    create_api_client,
)
from nourish_nest.assistant_ui import render_assistant
from nourish_nest.dashboard_ui import render_dashboard
from nourish_nest.grocery_ui import render_groceries
from nourish_nest.member_ui import render_members, render_nutrition
from nourish_nest.pantry_ui import render_pantry
from nourish_nest.planning_ui import render_planner
from nourish_nest.recipe_ui import render_recipes
from nourish_nest.ui_design import ASSETS, NAV_PAGES, apply_design, badge, brand, page_header
from nourish_nest.ui_labels import friendly_message, technical_details
from nourish_nest.ui_labels import labels as human_labels
from nourish_nest.ui_state import (
    navigate,
    remember_created_household,
    sync_household_selection,
)


def show_error(error: APIError) -> None:
    st.error(friendly_message(error.message))
    technical_details(
        error_code=error.code, request_ID=error.request_id, original_message=error.message
    )


def create_household_form(api: APIClient) -> None:
    with st.form("create_household"):
        name = st.text_input("Household name", max_chars=200, key="new_household_name")
        left, right = st.columns(2)
        timezone = left.text_input("Timezone", value="UTC", max_chars=64)
        currency = right.text_input(
            "Currency", value="USD", max_chars=3, help="Three-letter currency code"
        )
        submitted = st.form_submit_button("Create household", type="primary")
    if submitted:
        if not name.strip() or not timezone.strip() or len(currency.strip()) != 3:
            st.error("Enter a household name, timezone, and three-letter currency code.")
            return
        try:
            with st.spinner("Creating your household…"):
                household = api.create_household(name, timezone, currency)
            remember_created_household(st.session_state, household)
            st.rerun()
        except APIError as error:
            show_error(error)
            st.info(
                "This request was not retried automatically. Refresh the household list before submitting again."
            )


def quick_actions() -> None:
    st.subheader("Quick actions")
    actions = [
        ("Add member", "Household"),
        ("Add pantry item", "Pantry"),
        ("Create grocery list", "Grocery Lists"),
        ("Calculate nutrition", "Nutrition"),
        ("Browse recipes", "Recipes"),
        ("Preview meals with AI Assistant", "AI Assistant"),
    ]
    for column, (label, page) in zip(st.columns(3) + st.columns(3), actions, strict=True):
        column.button(
            label,
            key=f"action_{page}",
            on_click=navigate,
            args=(st.session_state, page, label),
            use_container_width=True,
        )
    st.caption("Manage members, recipes, nutrition, pantry inventory, and grocery lists.")


def dashboard_cards(counts: DashboardCounts) -> None:
    cards = [
        ("Household members", counts.members, "People in your household"),
        ("Recipes", counts.recipes, "Household and shared recipes"),
        ("Active pantry items", counts.active_pantry_items, "Active inventory lots"),
        ("Expiring items", counts.expiring_items, "Upcoming expiration"),
        ("Low-stock items", counts.low_stock_items, "Below saved stock thresholds"),
        ("Active grocery lists", counts.active_grocery_lists, "Lists marked active"),
    ]
    for start in (0, 3):
        for column, (label, value, caption) in zip(
            st.columns(3), cards[start : start + 3], strict=True
        ):
            with column.container(border=True):
                st.metric(label, value)
                st.caption(caption)
                if label in ("Expiring items", "Low-stock items"):
                    st.button(
                        f"View {label.lower()}",
                        on_click=navigate,
                        args=(st.session_state, "Pantry", label),
                        use_container_width=True,
                    )
                elif label == "Active grocery lists":
                    st.button(
                        "View active grocery lists",
                        on_click=navigate,
                        args=(st.session_state, "Grocery Lists", label),
                        use_container_width=True,
                    )
    if not any(counts.model_dump().values()):
        st.info(
            "Your household is ready. There is no dashboard data yet. Use Add member to get started."
        )


def selected_page(api: APIClient, household: Household) -> None:
    page = st.session_state["page"]
    page_header(page, household)
    if page == "Dashboard":
        render_dashboard(api, household, show_error, dashboard_cards, quick_actions)
    elif page == "Household":
        with st.container(border=True):
            st.subheader(household.name)
            technical_details(household_ID=household.id)
            st.write(f"Timezone: {household.timezone} · Currency: {household.currency}")
        st.caption("Change the selected household using the sidebar selector.")
        with st.expander(
            "Create another household", expanded=st.session_state.get("intent") == "New household"
        ):
            create_household_form(api)
        render_members(api, household, show_error)
    elif page == "Nutrition":
        render_nutrition(api, household, show_error)
    elif page == "Recipes":
        render_recipes(api, household, show_error)
    elif page == "Pantry":
        render_pantry(api, household, show_error)
    elif page == "Grocery Lists":
        render_groceries(api, household, show_error)
    elif page == "Meal Planner":
        render_planner(api, household, show_error)
    elif page == "AI Assistant":
        render_assistant(api, household, show_error)


def main() -> None:
    st.set_page_config(page_title="NourishNest", page_icon=str(ASSETS / "brand/favicon.svg"), layout="wide")
    apply_design()
    st.session_state.setdefault("page", "Dashboard")
    if "pending_page" in st.session_state:
        navigate(st.session_state, st.session_state.pop("pending_page"))
    with st.sidebar:
        brand()
        household_picker = st.container()
        st.radio("Workspace", NAV_PAGES, key="page", label_visibility="collapsed")
        st.divider()
    try:
        with create_api_client() as api:
            with st.sidebar:
                with st.spinner("Checking API…"):
                    health = api.health()
                badge("Connected", "success")
                technical_details(application_version=health.version, configured_provider=UISettings().ai_provider)
            with st.spinner("Loading households…"):
                households = sorted(api.households(), key=lambda h: (h.name.casefold(), str(h.id)))
            selected = sync_household_selection(st.session_state, households)
            labels = {
                str(key): value
                for key, value in human_labels(households, context=lambda h: h.timezone).items()
            }
            with household_picker:
                if households:
                    st.selectbox(
                        "Household", list(labels), format_func=labels.get, key="household_id"
                    )
                    selected = st.session_state["household_id"]
                    st.button(
                        "New household",
                        on_click=navigate,
                        args=(st.session_state, "Household", "New household"),
                    )
                else:
                    st.info("No households yet")
            if "success_message" in st.session_state:
                st.success(st.session_state.pop("success_message"))
            if selected is None:
                page_header(st.session_state["page"])
                st.subheader("Welcome home")
                st.write(
                    "Create your first household to bring meals, pantry stock, and grocery planning together."
                )
                create_household_form(api)
            else:
                selected_page(api, next(h for h in households if str(h.id) == selected))
    except APIError as error:
        st.sidebar.error("API connection needs attention")
        show_error(error)
        st.info(
            "Start FastAPI and refresh. Check APP_API_BASE_URL and the two-terminal instructions in README if port 8000 is unavailable."
        )
        st.button("Retry connection")
