"""Preview-only presentation. Only member reads and the assistant HTTP endpoint are callable."""

import re
from uuid import UUID

import streamlit as st
from pydantic import ValidationError

from nourish_nest.api_client import APIError, UISettings
from nourish_nest.assistant_client_models import AssistantInput, ConversationMessage
from nourish_nest.ui_design import badge, illustration, recipe_image
from nourish_nest.ui_labels import friendly_message, humanize, labels, validation_errors

EXAMPLES = (
    "Plan five vegetarian dinners under 600 calories.",
    "Use ingredients expiring soon and show what I need to buy.",
    "Plan quick vegan dinners under 30 minutes.",
    ("Plan 1 vegan dinner for 2 people within 30 minutes, prioritize expiring pantry items, "
     "and show what I need to buy."),
)
NUTRIENTS = (("calories", "Calories", "kcal"), ("protein_g", "Protein", "g"),
             ("carbohydrate_g", "Carbohydrate", "g"), ("fat_g", "Fat", "g"))


def new_conversation():
    return {"prompt": "", "preview": None, "context": [], "error": None,
            "continue": False, "last_submission": None}


def remember_turn(workspace, prompt, response):
    # Keep full user constraints; the UI caps each prompt at the context-message limit.
    workspace["context"] = (workspace["context"] + [
        ConversationMessage(role="user", content=prompt),
        ConversationMessage(role="assistant", content=response.assistant_message[:1000]),
    ])[-6:]


def set_example(workspace, prompt_key, continuation_key, prompt):
    workspace.update(new_conversation())
    workspace["prompt"] = prompt
    st.session_state[prompt_key] = prompt
    st.session_state[continuation_key] = False


def clear_conversation(workspace, prompt_key, continuation_key):
    set_example(workspace, prompt_key, continuation_key, "")


def save_field(workspace, field, key):
    workspace[field] = st.session_state[key]


def display_nutrition(nutrition):
    # Display only values supplied by the API, with no scaling or calculation in the UI.
    for field, name, unit in NUTRIENTS:
        value = getattr(nutrition, field)
        st.write(f"{name}: {value if value is not None else 'Unavailable'} {unit}")
    for warning in nutrition.warnings:
        st.warning(friendly_message(warning))


def internal_ids(value, path="response"):
    if isinstance(value, UUID):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from internal_ids(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from internal_ids(child, f"{path}[{index}]")


def render_result(result):
    st.subheader("Assistant summary")
    with st.chat_message("assistant", avatar="🌿"):
        st.write(friendly_message(result.assistant_message))
    if result.status == "clarification":
        st.subheader("Clarification needed")
        st.info(friendly_message(result.assistant_message))
        st.caption("Add missing details and enable Continue clarification, or start a complete new request.")
    intent = result.interpreted_constraints
    st.subheader("What I understood")
    details = [f"Days: {intent.days or 'Please specify'}",
               f"Meal: {humanize(intent.meal) if intent.meal else 'Please specify'}",
               f"Servings per meal: {intent.servings or 'Please specify'}"]
    if intent.diets:
        details.append("Diet: " + ", ".join(humanize(diet) for diet in intent.diets))
    if intent.maximum_calories is not None:
        qualifier = "Under" if intent.strict_calorie_limit else "At most"
        details.append(f"{qualifier} {intent.maximum_calories} calories per serving")
    if intent.maximum_cooking_minutes is not None:
        details.append(f"Cooking time: up to {intent.maximum_cooking_minutes} minutes")
    details.extend([f"Shopping preview: {'Yes' if intent.groceries else 'No'}",
                    f"Adult target comparison: {'Yes' if intent.compare_target else 'No'}",
                    f"Repeated recipes: {'Allowed' if intent.allow_repeats else 'Not requested'}"])
    for detail in details:
        st.write(friendly_message(detail))
    if result.proposed_plan:
        st.subheader("Your seven-day meal preview")
        for day in result.proposed_plan:
            with st.container(border=True):
                st.write(f"Day {day.day}")
                for meal in day.meals:
                    st.write(friendly_message(
                        f"{humanize(meal.slot)}: {meal.recipe_name} · {meal.desired_servings} servings"
                    ))
                if not day.meals:
                    st.caption("No meal planned for this day.")
    if result.recommendations_used:
        st.subheader("Recipes behind this plan")
        for recipe in result.recommendations_used:
            with st.container(border=True):
                illustration(recipe_image(recipe.cuisine))
                st.subheader(friendly_message(recipe.recipe_name))
                st.caption("System recipe" if recipe.system_recipe else "Household recipe")
                st.write(friendly_message(recipe.classification))
                st.write(f"Pantry coverage: {recipe.coverage_percentage}%")
                st.write(f"Preparation: {recipe.preparation_minutes} min · Cooking: {recipe.cooking_minutes} min")
                st.write(friendly_message(recipe.explanation))
                for food in recipe.missing_ingredients:
                    st.write(friendly_message(f"Missing: {food.food_name} · {food.missing_quantity} {food.unit}"))
                for food in recipe.expiring_ingredients:
                    st.write(friendly_message(f"Use soon: {food.food_name} · expires {food.earliest_expiration}"))
                st.caption("Nutrition per serving")
                display_nutrition(recipe.nutrition_per_serving)
                for warning in recipe.warnings:
                    st.warning(friendly_message(warning.message))
    if result.nutrition_summary:
        st.subheader("Nutrition for planned meals")
        st.caption("Totals include all planned servings and only the selected meals, not a complete individual diet.")
        with st.expander("Daily nutrition totals"):
            for number, daily in enumerate(result.nutrition_summary.daily, 1):
                st.write(f"Day {number}")
                display_nutrition(daily)
        st.write("Weekly total")
        display_nutrition(result.nutrition_summary.weekly)
    if result.member_nutrition_target:
        target = result.member_nutrition_target
        st.subheader("Selected adult member's daily target")
        st.write(f"Estimated BMR: {target.bmr_calories} kcal · Maintenance: {target.maintenance_calories} kcal")
        st.write(f"Calorie target: {target.target_calories} kcal")
        st.write(f"Protein: {target.macros.protein_g} g · Carbohydrate: {target.macros.carbohydrate_g} g · Fat: {target.macros.fat_g} g")
        for warning in target.warnings:
            st.warning(friendly_message(warning))
    if result.grocery_shortage_preview:
        preview = result.grocery_shortage_preview
        st.subheader("What you may need to buy")
        st.caption(f"Pantry availability is a point-in-time estimate as of {preview.calculation_as_of.isoformat()}. Nothing is reserved.")
        for food in preview.requirements:
            with st.container(border=True):
                st.write(friendly_message(food.food_name))
                st.write(f"Needed: {food.required_quantity} {food.canonical_unit}")
                st.write(f"Pantry available: {food.available_quantity} {food.canonical_unit}")
                st.write(f"To buy: {food.shortage_quantity} {food.canonical_unit}")
                st.caption("Purchase needed" if food.purchase_required else "Covered by pantry")
                for source in food.sources:
                    st.caption(friendly_message(f"From {source.recipe_name}: {source.required_quantity} {food.canonical_unit}"))
        if not preview.requirements:
            st.info("No quantities were returned. Review calculation warnings before drawing conclusions.")
        for warning in preview.warnings:
            st.warning(friendly_message(warning.message))
    st.subheader("Warnings and safety")
    for warning in result.warnings:
        st.warning(friendly_message(warning))
    if result.confirmation_required:
        st.warning("Confirmation is required. This page cannot confirm or perform write actions.")
    else:
        st.caption("No confirmation action is needed for this preview.")
    st.info("No pantry or grocery data has been changed. Nutrition is informational, not medical advice.")
    with st.expander("Technical details", expanded=False):
        st.text(f"Request ID: {result.request_id}")
        st.text(f"Calculation version: {result.calculation_version}")
        st.text(f"Provider/model version: {result.model_version}")
        if result.grocery_shortage_preview:
            st.text(f"Shortage calculation: {result.grocery_shortage_preview.calculation_version}")
        if result.member_nutrition_target:
            st.text(f"Nutrition calculation: {result.member_nutrition_target.calculation_version}")
        st.text("Safe tool trace")
        for trace in result.tool_trace:
            st.text(f"{trace.name}: {trace.status} ({trace.duration_ms} ms)")
        for name, identifier in internal_ids(result.model_dump()):
            st.text(f"{name}: {identifier}")


def render_assistant(api, household, show_error):
    st.write("Describe the meals you want, then review a meal-planning preview. Nothing will be saved or purchased.")
    st.caption("Nutrition is informational, not medical advice. Personal allergy checks use the selected member's saved profile.")
    if household is None:
        st.info("Select or create a household to preview meals.")
        return
    home = household.id
    try:
        with st.spinner("Loading household members…"):
            members = api.members(home)
    except APIError as error:
        show_error(error)
        st.button("Refresh members")
        return
    member_names = {"household": "Plan for the household (no personal allergy filter)", **labels(members)}
    member_key = f"assistant_member_{home}"
    saved_member = f"assistant_selected_member_{home}"
    st.session_state.setdefault(saved_member, "household")
    if st.session_state.get(saved_member) not in member_names:
        st.session_state[saved_member] = "household"
    st.session_state.setdefault(member_key, st.session_state[saved_member])
    if st.session_state[member_key] not in member_names:
        st.session_state[member_key] = "household"
    selection = st.selectbox("Plan for (optional)", list(member_names), format_func=member_names.get, key=member_key)
    st.session_state[saved_member] = selection
    selected = None if selection == "household" else selection
    prefix = f"assistant_{home}_{selected}"
    workspace = st.session_state.setdefault(prefix, new_conversation())
    prompt_key, continue_key = f"{prefix}_input", f"{prefix}_continue"
    st.session_state.setdefault(prompt_key, workspace["prompt"])
    st.session_state.setdefault(continue_key, workspace["continue"])
    result = workspace["preview"]
    settings = UISettings()
    local_model = settings.ai_provider == "ollama" or bool(result and result.model_version.startswith("ollama:"))
    if local_model:
        model = (result.model_version.removeprefix("ollama:").split("@", 1)[0]
                 if result and result.model_version.startswith("ollama:") else settings.ai_model)
        if model and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}", model):
            model = "Check the local model name in configuration."
        with st.expander("Technical details", expanded=False):
            st.info("Local Ollama mode")
            st.caption(f"Model: {friendly_message(model) if model else 'Choose an installed local model in configuration.'}")
        st.caption("Describe days, one meal slot, servings, and any supported preferences. Review the interpreted constraints before using a preview.")
    elif settings.ai_provider == "fake" or (result and result.model_version == "fake-intent-v1"):
        badge("Local preview mode")
        st.caption("A deterministic preview, not a live generative model.")
        with st.expander("Technical details", expanded=False):
            st.info("Local demo mode — a limited deterministic interpreter, not a generative model. No external model calls or paid AI credits.")
    if not local_model:
        st.caption("For a complete local demo request, include days, one meal slot, and servings. Short examples may ask for clarification; unsupported wording needs a complete rewrite.")
    with st.expander("Try an example"):
        for index, prompt in enumerate(EXAMPLES):
            st.button(prompt, key=f"{prefix}_example_{index}", use_container_width=True,
                      on_click=set_example, args=(workspace, prompt_key, continue_key, prompt))
    prompt = st.text_area("What would you like to plan?", key=prompt_key, max_chars=1000,
                          height=150, placeholder="Plan 1 vegan dinner for 2 people…",
                          on_change=save_field, args=(workspace, "prompt", prompt_key))
    continuation = st.checkbox("Continue clarification using previous messages", key=continue_key,
                               on_change=save_field, args=(workspace, "continue", continue_key),
                               help="Leave off for a complete new request. Clear conversation to remove earlier constraints.")
    submitted = st.button("Preview meals", type="primary", disabled=not prompt.strip())
    st.button("Clear conversation/preview", on_click=clear_conversation,
              args=(workspace, prompt_key, continue_key))
    if submitted:
        try:
            data = AssistantInput(user_message=prompt, member_id=selected,
                                  conversation_context=workspace["context"] if continuation else [])
            signature = data.model_dump_json()
            if workspace["last_submission"] != signature or workspace["error"] is not None:
                with st.spinner("Building your meal plan…"):
                    response = api.assistant_preview(home, data)
                if response.household_id != home:
                    raise APIError("invalid_response", "The preview did not match the selected household.")
                workspace["preview"] = response
                workspace["error"] = None
                workspace["last_submission"] = signature
                workspace["context"] = list(data.conversation_context)
                remember_turn(workspace, prompt, response)
        except APIError as error:
            workspace["error"] = error
        except ValidationError as error:
            validation_errors(error)
    if workspace["error"]:
        error = workspace["error"]
        if error.code == "unsafe_assistant_request":
            st.subheader("Safety refusal")
        show_error(error)
        st.caption("Your request and last successful preview have been kept. No data was changed.")
        if error.code == "assistant_provider_unavailable":
            if local_model:
                st.info("Start local Ollama and check that your configured model is already installed, then retry this request. No model is downloaded automatically. You can also return to fake demo mode.")
            else:
                st.info("For the free local demo, start FastAPI with APP_AI_PROVIDER=fake, then try again.")
    if workspace["preview"]:
        if workspace["error"]:
            st.info("Previous successful preview — the latest request did not complete.")
        render_result(workspace["preview"])
