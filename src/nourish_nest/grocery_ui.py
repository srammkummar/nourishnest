"""Grocery workflows through HTTP only; calculations remain in FastAPI."""

from collections.abc import Callable
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

import nourish_nest.grocery_client_models as wire
from nourish_nest.api_client import APIClient, APIError, Household
from nourish_nest.pantry_ui import UNITS, choose_food
from nourish_nest.ui_labels import (
    humanize,
    labels,
    technical_details,
    unit_label,
    validation_errors,
)

STATUSES = ("draft", "active", "completed", "archived")
SECTIONS = ("Lists", "Manual items", "Recipe planning", "Purchase")


def new_workspace():
    return {
        "lists": None,
        "records": {},
        "recipes": None,
        "preview": None,
        "foods": {},
        "search": {},
        "epoch": 0,
        "notice": None,
        "refresh_purchase": False,
    }


def list_workspace(workspace, list_id):
    return workspace["records"].setdefault(
        str(list_id),
        {
            "detail": None,
            "items": None,
            "generation": None,
            "purchase": None,
            "generation_result": None,
            "purchase_result": None,
        },
    )


def finish(workspace, record, message):
    workspace.update(lists=None, epoch=workspace["epoch"] + 1, search={}, notice=message)
    if record is not None:
        record.update(detail=None, items=None)


def item_rows(items):
    return [
        {
            "Item": i.display_name,
            "Required": str(i.required_quantity),
            "Unit": i.required_unit,
            "Purchased": str(i.purchased_quantity),
            "Status": "✓ Purchased"
            if i.checked
            else "◐ Partial"
            if i.purchased_quantity
            else "○ Needed",
            "Category": i.category or "",
            "Source": humanize(i.source_type),
        }
        for i in items
    ]


def requirement_rows(result):
    rows = []
    for requirement in result.requirements:
        row = {
            "Food": requirement.food_name,
            "Required": str(requirement.required_quantity),
            "Unit": requirement.canonical_unit,
        }
        if isinstance(requirement, wire.Shortage):
            row.update(
                {
                    "Pantry available": str(requirement.available_quantity),
                    "To purchase": str(requirement.shortage_quantity),
                    "Coverage": "Purchase required"
                    if requirement.purchase_required
                    else "Fully covered",
                }
            )
        rows.append(row)
    return rows


def render_warnings(warnings):
    for warning in warnings:
        st.warning(warning.message)
        technical_details(
            code=warning.code,
            food_ID=warning.food_id,
            recipe_ID=warning.recipe_id,
            pantry_lot_ID=warning.pantry_item_id,
        )


def render_preview(result):
    st.subheader("Preview result")
    st.caption(f"Calculation: {result.calculation_version}")
    source_names = {
        source.recipe_id: source.recipe_name
        for requirement in result.requirements
        for source in requirement.sources
    }
    st.caption(
        "Selection used: "
        + "; ".join(
            f"{source_names.get(selection.recipe_id, 'Selected recipe')}: {selection.desired_servings} servings"
            for selection in result.recipes
        )
    )
    technical_details(
        recipe_selections="; ".join(
            f"{selection.recipe_id}: {selection.desired_servings}" for selection in result.recipes
        )
    )
    if isinstance(result, wire.ShortageResult):
        st.info(
            f"Pantry availability is a point-in-time estimate as of {result.calculation_as_of.isoformat()}. Stock is not reserved; generation recalculates shortages."
        )
    if result.requirements:
        st.dataframe(requirement_rows(result), hide_index=True, use_container_width=True)
    else:
        st.info("No requirements were returned. Review any calculation warnings.")
    for requirement in result.requirements:
        with st.expander(f"Sources: {requirement.food_name} · {requirement.canonical_unit}"):
            st.dataframe(
                [
                    {
                        "Recipe": s.recipe_name,
                        "Scaled original quantity": f"{s.scaled_quantity} {s.original_unit}",
                        "Contribution": f"{s.required_quantity} {requirement.canonical_unit}",
                    }
                    for s in requirement.sources
                ],
                hide_index=True,
            )
            if isinstance(requirement, wire.Shortage) and requirement.pantry_lots:
                st.dataframe(
                    [
                        {
                            "Food": requirement.food_name,
                            "Original": f"{p.quantity} {p.original_unit}",
                            "Available contribution": f"{p.available_quantity} {requirement.canonical_unit}",
                            "Expiration": str(p.expiration_date or "Not set"),
                        }
                        for p in requirement.pantry_lots
                    ],
                    hide_index=True,
                )
    render_warnings(result.warnings)


def list_management(api, home, workspace, record, prefix):
    with st.expander(
        "Create grocery list",
        expanded=workspace.get("open_create", False) or not workspace["lists"],
    ):
        with st.form(f"{prefix}_create"):
            name = st.text_input("New list name", max_chars=200)
            status = st.selectbox("New list status", STATUSES, format_func=humanize)
            submitted = st.form_submit_button("Create list", type="primary")
        st.caption(
            "If saving times out, refresh your lists and check whether the new list exists before trying again."
        )
        if submitted:
            saved = api.create_grocery_list(home, wire.ListInput(name=name, status=status))
            workspace["pending_selection"] = str(saved.id)
            workspace["open_create"] = False
            finish(workspace, None, f"Created {saved.name}.")
            st.rerun()
    if record is None:
        return
    listing = record["detail"]
    with st.form(f"{prefix}_{listing.id}_{listing.version}_edit"):
        name = st.text_input("List name", listing.name, max_chars=200)
        status = st.selectbox(
            "List status", STATUSES, format_func=humanize, index=STATUSES.index(listing.status)
        )
        technical_details(version=listing.version)
        submitted = st.form_submit_button("Save list", type="primary")
    if submitted:
        api.update_grocery_list(
            home,
            listing.id,
            wire.ListUpdate(name=name, status=status, expected_version=listing.version),
        )
        finish(workspace, record, f"Updated {name}.")
        st.rerun()
    with st.expander("Delete this grocery list"):
        confirmed = st.checkbox(
            "Confirm deletion of this list and all its items",
            key=f"{prefix}_{listing.id}_{listing.version}_delete",
        )
        if st.button("Delete list", disabled=not confirmed):
            api.delete_grocery_list(home, listing.id, listing.version)
            workspace["records"].pop(str(listing.id), None)
            finish(workspace, None, f"Deleted {listing.name}.")
            st.rerun()


def manual_items(api, home, workspace, record, prefix):
    listing = record["detail"]
    manual = [i for i in record["items"] if i.source_type == "manual"]
    st.caption(
        "Enter the item name and amount needed. Linking a food and adding a category are optional."
    )
    selection = st.selectbox(
        "Manual item",
        [None, *[i.id for i in manual]],
        format_func=lambda value: (
            "Add a new item"
            if value is None
            else labels(
                manual,
                name=lambda i: i.display_name,
                context=lambda i: (
                    f"{i.required_quantity} {i.required_unit} · {i.category or 'Uncategorised'}"
                ),
            )[value]
        ),
        key=f"{prefix}_manual",
    )
    item = next((i for i in manual if i.id == selection), None)
    token = f"{prefix}_{selection}_{item.version if item else 'new'}"
    attach = st.checkbox(
        "Link to stored food", value=bool(item and item.food_id), key=f"{token}_attach"
    )
    food_id = None
    if attach:
        if item and item.food_id and token not in workspace["search"]:
            food = api.stored_food(item.food_id)
            workspace["search"][token] = [food]
            workspace["foods"][food.id] = food
        food_id = choose_food(api, workspace, token)
    with st.form(f"{token}_edit"):
        name = st.text_input("Item name", item.display_name if item else "", max_chars=200)
        quantity = st.text_input("Required quantity", str(item.required_quantity) if item else "1")
        units = list(dict.fromkeys([item.required_unit, *UNITS])) if item else UNITS
        unit = st.selectbox(
            "Required unit",
            units,
            format_func=unit_label,
            disabled=bool(item and item.purchased_quantity),
        )
        category = st.text_input(
            "Category (optional)", (item.category or "") if item else "", max_chars=100
        )
        if item:
            st.caption(
                f"Purchased: {item.purchased_quantity} {item.required_unit}. Record additional purchases in Purchase."
            )
        submitted = st.form_submit_button("Save item" if item else "Add item")
    if submitted:
        if attach and food_id is None:
            st.error("Select a stored food or turn off the food link.")
            return
        values = {
            "food_id": food_id,
            "display_name": name,
            "required_quantity": quantity,
            "required_unit": unit,
            "category": category or None,
        }
        if item:
            values.update(
                purchased_quantity=item.purchased_quantity,
                checked=item.checked,
                source_type=item.source_type,
                source_reference_id=item.source_reference_id,
            )
            api.update_grocery_item(
                home, listing.id, item.id, wire.ItemUpdate(**values, expected_version=item.version)
            )
        else:
            api.create_grocery_item(home, listing.id, wire.ItemInput(**values))
        finish(workspace, record, f"Saved {name}.")
        st.rerun()
    if item:
        with st.expander("Delete this item"):
            confirm = st.checkbox("Confirm deletion of this item", key=f"{token}_confirm")
            if st.button("Delete item", disabled=not confirm):
                api.delete_grocery_item(home, listing.id, item.id, item.version)
                finish(workspace, record, f"Deleted {item.display_name}.")
                st.rerun()
    st.caption(
        "If adding an item times out, refresh and check the list before trying again. Items from recipes keep their original recipe details."
    )


def submit_generation(api, home, workspace, record):
    result = api.generate_grocery_list(home, record["detail"].id, record["generation"])
    record.update(generation=None, generation_result=result)
    finish(
        workspace,
        record,
        "Recipe generation saved."
        if not result.replayed
        else "Existing recipe generation recovered.",
    )
    st.rerun()


def generation_result(result, recipes=()):
    st.subheader("Saved generation")
    st.caption(f"Saved at {result.calculation_as_of.isoformat()}")
    technical_details(
        generation_run_ID=result.generation_run_id,
        version=result.grocery_list_version,
        calculation=result.calculation_version,
    )
    if result.created_items:
        st.dataframe(item_rows(result.created_items), hide_index=True)
        technical_details(
            recipe_lineage="\n".join(
                f"{item.id}: recipe {source.recipe_id}, ingredient {source.recipe_ingredient_id}"
                for item in result.created_items
                for source in item.recipe_sources
            )
        )
        st.dataframe(
            [
                {
                    "Item": item.display_name,
                    "Recipe": next(
                        (r.name for r in recipes if r.id == source.recipe_id),
                        "Saved recipe contribution",
                    ),
                    "Recipe requirement before pantry subtraction": f"{source.required_quantity} {source.canonical_unit}",
                }
                for item in result.created_items
                for source in item.recipe_sources
            ],
            hide_index=True,
        )
    else:
        st.info("Generation saved with zero items. No positive purchase shortages were returned.")
    if not result.warnings_available:
        st.warning(
            "This is a replay. Original calculation warnings are not stored by the API and cannot be recovered."
        )
    render_warnings(result.warnings)


def recipe_planning(api, home, workspace, record, prefix):
    if workspace["recipes"] is None:
        workspace["recipes"] = [r for r in api.recipes(home) if r.household_id in (None, home)]
    recipes = {r.id: r for r in workspace["recipes"]}
    if not recipes:
        st.info("No readable recipes yet. Create a household recipe first.")
        return
    if f"{prefix}_recipes" not in st.session_state:
        st.session_state[f"{prefix}_recipes"] = [
            r for r in workspace.get("selected_recipes", []) if r in recipes
        ]
    else:
        st.session_state[f"{prefix}_recipes"] = [
            r for r in st.session_state[f"{prefix}_recipes"] if r in recipes
        ]
    selected = st.multiselect(
        "Recipes",
        list(recipes),
        format_func=labels(
            recipes.values(),
            context=lambda r: "System recipe" if r.household_id is None else "Household recipe",
        ).get,
        key=f"{prefix}_recipes",
    )
    workspace["selected_recipes"] = selected
    servings_state = workspace.setdefault("servings", {})
    selections = []
    for recipe_id in selected:
        servings = st.text_input(
            f"Desired servings: {recipes[recipe_id].name}",
            servings_state.get(recipe_id, str(recipes[recipe_id].servings)),
            key=f"{prefix}_{recipe_id}_servings",
        )
        servings_state[recipe_id] = servings
        selections.append({"recipe_id": recipe_id, "desired_servings": servings})
    a, b = st.columns(2)
    requirements = a.button("Preview recipe requirements")
    shortages = b.button("Preview pantry shortages")
    if requirements or shortages:
        data = wire.RequirementsInput(recipes=selections)
        with st.spinner("Calculating preview…"):
            workspace["preview"] = (
                api.grocery_requirements(home, data)
                if requirements
                else api.grocery_shortages(home, data)
            )
    if workspace["preview"] is not None:
        render_preview(workspace["preview"])
    if record is None:
        st.info("Select or create a grocery list to save a generation.")
        return
    listing = record["detail"]
    st.subheader("Save shopping needs")
    st.caption(
        f"Target: {listing.name}. Only one successful recipe generation is supported per list. Existing manual items remain."
    )
    if record["generation"] is not None:
        st.info(
            "Your recipe choices are saved for retry. Retry the original request, or reset it to start again after checking the list."
        )
        if st.button("Retry generation"):
            submit_generation(api, home, workspace, record)
        if st.button("Reset generation request"):
            record["generation"] = None
            st.rerun()
    elif record["generation_result"] is None:
        if st.button(
            "Generate into selected list", disabled=listing.status not in ("draft", "active")
        ):
            record["generation"] = wire.GenerationInput(
                recipes=selections,
                expected_list_version=listing.version,
                idempotency_key=uuid4().hex,
            )
            submit_generation(api, home, workspace, record)
        if listing.status not in ("draft", "active"):
            st.info("Generation requires a draft or active list.")
    if record["generation_result"]:
        generation_result(record["generation_result"], workspace["recipes"])


def submit_purchase(api, home, workspace, record):
    pending = record["purchase"]
    result = api.purchase_grocery_item(
        home, record["detail"].id, pending["item"].id, pending["payload"]
    )
    record.update(purchase=None, purchase_result=result)
    workspace["refresh_purchase"] = True
    pantry_workspace = st.session_state.get(f"pantry_{home}")
    if pantry_workspace is not None:
        pantry_workspace["snapshot"] = None
    finish(
        workspace,
        record,
        "Purchase recorded."
        if not result.replayed
        else "Existing purchase recovered; no additional stock created.",
    )
    st.rerun()


def purchase(api, home, workspace, record, prefix):
    receipt = record["purchase_result"]
    if receipt:
        st.success(
            f"Recorded purchase: {receipt.purchased_quantity} {receipt.purchased_unit}. Purchased total: {receipt.purchased_total} {receipt.item_unit}."
        )
        st.caption(f"List status: {humanize(receipt.grocery_list_status)}")
        technical_details(
            purchase_event_ID=receipt.purchase_event_id,
            version=receipt.item_version,
            pantry_lot_ID=receipt.pantry_item_id,
            transaction_ID=receipt.pantry_transaction_id,
        )
        if receipt.purchase_price is not None:
            st.write(f"Total price: {receipt.purchase_price} {receipt.currency}")
    pending = record["purchase"]
    if pending is None:
        if not record["items"]:
            st.info("Add or generate grocery items before recording a purchase.")
            return
        items = {i.id: i for i in record["items"]}
        selected = st.selectbox(
            "Item to purchase",
            list(items),
            format_func=labels(
                items.values(),
                name=lambda i: i.display_name,
                context=lambda i: (
                    f"{i.purchased_quantity}/{i.required_quantity} {i.required_unit} · {i.category or humanize(i.source_type)}"
                ),
            ).get,
            key=f"{prefix}_item",
        )
        if st.button("Start purchase"):
            record["purchase"] = {"item": items[selected], "key": uuid4().hex, "payload": None}
            st.rerun()
        return
    item = pending["item"]
    st.subheader(f"Purchase: {item.display_name}")
    st.caption("Enter the amount you bought this time, not the total of all previous purchases.")
    if st.button("Reset purchase request"):
        record["purchase"] = None
        record.update(detail=None, items=None)
        st.rerun()
    if pending["payload"] is not None:
        st.info(
            "Retry sends the same purchase and pantry choices without adding stock twice. Check inventory before resetting to a different purchase."
        )
        if st.button("Retry purchase"):
            submit_purchase(api, home, workspace, record)
        return
    token = f"{prefix}_{pending['key']}"
    intake = st.checkbox(
        "Add purchase to pantry", disabled=item.food_id is None, key=f"{token}_intake"
    )
    if item.food_id is None:
        st.caption(
            "This item has no stored-food link. It can be purchased but cannot be added to pantry."
        )
    locations = (
        labels(api.pantry_locations(home), context=lambda r: humanize(r.location_type))
        if intake
        else {}
    )
    with st.form(f"{token}_form"):
        amount = st.text_input(
            "Amount bought this time",
            "1",
            help="Enter a positive amount. For example, 0.5 kg means half a kilogram.",
        )
        unit = st.selectbox(
            "Purchased unit",
            list(dict.fromkeys([item.required_unit, *UNITS])),
            format_func=unit_label,
        )
        overpurchase = st.checkbox("Explicitly allow overpurchase")
        location = (
            st.selectbox("Pantry location", list(locations), format_func=locations.get)
            if intake
            else None
        )
        expiration = st.date_input("Expiration date (optional)", value=None)
        price = st.text_input(
            "Total purchase price (optional)",
            help="Total price for this increment, in household currency.",
        )
        submitted = st.form_submit_button("Record purchase", type="primary")
    if submitted:
        pending["payload"] = wire.PurchaseInput(
            purchased_quantity=amount,
            purchased_unit=unit,
            expected_item_version=item.version,
            idempotency_key=pending["key"],
            add_to_pantry=intake,
            pantry_location_id=location,
            expiration_date=expiration,
            purchase_price=price if price.strip() else None,
            allow_overpurchase=overpurchase,
        )
        submit_purchase(api, home, workspace, record)


def render_groceries(api: APIClient, household: Household, show_error: Callable):
    st.write("Plan your shopping, check what is already at home, and record purchases as you go.")
    with st.expander("Shopping guide", expanded=True):
        st.markdown(
            "1. **Create/select list** in Lists.\n2. **Select recipes** in Recipe planning.\n3. **Preview needs** to see ingredients.\n4. **Check pantry shortages** to see what to buy.\n5. **Generate list** to save those shortages.\n6. **Record purchases** in Purchase, optionally adding food to your pantry."
        )
    home = household.id
    prefix = f"groceries_{home}"
    workspace = st.session_state.setdefault(prefix, new_workspace())
    for field, default in (("section", "Lists"), ("filter", "All")):
        st.session_state.setdefault(f"{prefix}_{field}", workspace.get(field, default))
    intent = st.session_state.get("intent")
    if intent in ("Create grocery list", "Active grocery lists"):
        st.session_state[f"{prefix}_section"] = "Lists"
        st.session_state[f"{prefix}_filter"] = (
            "active" if intent == "Active grocery lists" else "All"
        )
        workspace["open_create"] = intent == "Create grocery list"
        st.session_state["intent"] = ""
    if workspace["notice"]:
        st.success(workspace["notice"])
        workspace["notice"] = None
    if st.button("Refresh groceries"):
        workspace.update(lists=None, recipes=None, epoch=workspace["epoch"] + 1)
        for record in workspace["records"].values():
            record.update(detail=None, items=None)
    try:
        if workspace["refresh_purchase"]:
            # A refresh failure must never turn a saved purchase back into a pending write.
            workspace["pantry_summary"] = api.pantry_overview(home)
            workspace["dashboard"] = api.dashboard(home)
            workspace["refresh_purchase"] = False
        if workspace["lists"] is None:
            with st.spinner("Loading grocery lists…"):
                workspace["lists"] = api.grocery_lists(home)
        if "pending_selection" in workspace:
            st.session_state[f"{prefix}_selected"] = workspace.pop("pending_selection")
            st.session_state[f"{prefix}_filter"] = "All"
        status = st.selectbox(
            "Filter list status", ["All", *STATUSES], format_func=humanize, key=f"{prefix}_filter"
        )
        workspace["filter"] = status
        available = {
            str(r.id): r for r in workspace["lists"] if status == "All" or r.status == status
        }
        record = None
        if available:
            if st.session_state.get(f"{prefix}_selected") not in available:
                st.session_state[f"{prefix}_selected"] = (
                    workspace.get("selected")
                    if workspace.get("selected") in available
                    else next(iter(available))
                )
            # Use actual display strings as options so browser labels change with records.
            picker_options = {
                label: str(key)
                for key, label in labels(
                    available.values(), context=lambda r: humanize(r.status)
                ).items()
            }
            selected_option = st.selectbox(
                "Grocery list",
                list(picker_options),
                index=list(available).index(st.session_state[f"{prefix}_selected"]),
                # Recreate labels after refresh/write; Streamlit can retain old formatted labels.
                key=f"{prefix}_picker_{workspace['epoch']}",
            )
            selected = picker_options[selected_option]
            st.session_state[f"{prefix}_selected"] = selected
            workspace["selected"] = selected
            record = list_workspace(workspace, selected)
            if record["detail"] is None:
                record["detail"] = api.grocery_list(home, available[selected].id)
            if record["items"] is None:
                record["items"] = api.grocery_items(home, available[selected].id)
            listing, items = record["detail"], record["items"]
            st.subheader(f"{listing.name} · {listing.status.title()}")
            technical_details(list_ID=listing.id, version=listing.version)
            checked, total = sum(i.checked for i in items), len(items)
            st.progress(
                checked * 100 // total if total else 0, text=f"Purchased items: {checked} / {total}"
            )
            if items:
                st.dataframe(item_rows(items), hide_index=True, use_container_width=True)
            else:
                st.info("This list is empty. Add manual items or generate recipe shortages.")
        else:
            st.info("No grocery lists match. Create a list or change the status filter.")
        section = st.radio("Grocery section", SECTIONS, horizontal=True, key=f"{prefix}_section")
        workspace["section"] = section
        form_prefix = f"{prefix}_{workspace['epoch']}_{record['detail'].id if record else 'none'}"
        if section == "Lists":
            list_management(api, home, workspace, record, form_prefix)
        elif section == "Recipe planning":
            recipe_planning(api, home, workspace, record, prefix)
        elif record is None:
            st.info("Select or create a grocery list first.")
        elif section == "Manual items":
            manual_items(api, home, workspace, record, form_prefix)
        else:
            purchase(api, home, workspace, record, form_prefix)
    except ValidationError as error:
        validation_errors(error)
    except APIError as error:
        show_error(error)
        if error.code == "stale_grocery_version":
            st.info(
                "Refresh groceries to load current versions. Reset a pending request only when you intend to submit a new change."
            )
        elif error.code == "grocery_generation_exists":
            st.info(
                "This list already has a recipe generation. Regeneration and replacement are not supported; use another list."
            )
        elif error.code == "idempotency_conflict":
            st.info(
                "This key was used with a different request. Check the saved list or purchase before explicitly resetting the pending request."
            )
