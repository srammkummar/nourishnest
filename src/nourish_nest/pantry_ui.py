"""Household pantry presentation. Stock decisions belong exclusively to the HTTP API."""

from collections.abc import Callable
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from nourish_nest.api_client import APIClient, APIError, Household
from nourish_nest.pantry_client_models import (
    AdjustmentInput,
    ConsumeInput,
    LocationInput,
    PantryItemInput,
    PantryLot,
    StockRuleInput,
    TransferInput,
)
from nourish_nest.ui_design import badge, steps
from nourish_nest.ui_labels import (
    food_labels,
    humanize,
    labels,
    technical_details,
    unit_label,
    validation_errors,
)

UNITS = ("g", "kg", "oz", "lb", "ml", "l", "cup", "tbsp", "tsp", "item")
SECTIONS = ("Inventory", "Add item", "Locations", "Actions", "Low-stock rules", "History")
FILTERS = ("All", "Active", "Expiring soon", "Expired", "Depleted", "Discarded", "Low stock")


def new_workspace() -> dict:
    return {"snapshot": None, "foods": {}, "search": {}, "epoch": 0, "action": None, "notice": None}


def load_snapshot(api: APIClient, home, workspace: dict) -> dict:
    if workspace["snapshot"] is None:
        # These legacy reads may mark lots expired. Never calculate expiration locally.
        summary = api.pantry_overview(home)
        expiring = api.pantry_expiring(home)
        expired = api.pantry_expired(home)
        low_stock = api.pantry_low_stock(home)
        snapshot = {
            "summary": summary,
            "expiring": expiring,
            "expired": expired,
            "low_stock": low_stock,
            "rules": api.pantry_stock_rules(home),
            "locations": api.pantry_locations(home),
            "items": api.pantry_items(home),
        }
        food_ids = {i.food_id for i in snapshot["items"]} | {r.food_id for r in snapshot["rules"]}
        for food_id in sorted(food_ids, key=str):
            if food_id not in workspace["foods"]:
                workspace["foods"][food_id] = api.stored_food(food_id)
        workspace["snapshot"] = snapshot
    return workspace["snapshot"]


def finish(workspace: dict, message: str) -> None:
    workspace.update(
        snapshot=None, action=None, search={}, notice=message, epoch=workspace["epoch"] + 1,
        load_requested=True,
    )


def filtered_lots(snapshot: dict, foods: dict, query: str, location, view: str) -> list[PantryLot]:
    expiring = {i.id for i in snapshot["expiring"]}
    expired = {i.id for i in snapshot["expired"]}
    low_stock = {r.food_id for r in snapshot["low_stock"]}
    return sorted(
        [
            i
            for i in snapshot["items"]
            if (location is None or i.location_id == location)
            and query.casefold() in foods[i.food_id].name.casefold()
            and (
                view == "All"
                or (view == "Expiring soon" and i.id in expiring)
                or (view == "Expired" and i.id in expired)
                or (view == "Low stock" and i.food_id in low_stock)
                or (view in ("Active", "Depleted", "Discarded") and i.status == view.lower())
            )
        ],
        key=lambda i: (foods[i.food_id].name.casefold(), str(i.id)),
    )


def inventory_rows(lots: list[PantryLot], snapshot: dict, foods: dict) -> list[dict]:
    locations = labels(snapshot["locations"], context=lambda r: humanize(r.location_type))
    expiring = {i.id for i in snapshot["expiring"]}
    expired = {i.id for i in snapshot["expired"]}
    return [
        {
            "Food": foods[i.food_id].name,
            "Location": locations.get(i.location_id, "Unknown location"),
            "Status": f"{'🔴' if i.id in expired else '🟠' if i.id in expiring else '•'} {i.status.title()}",
            "Expiration alert": "Expired"
            if i.id in expired
            else "Expiring soon"
            if i.id in expiring
            else "",
            "Quantity": str(i.quantity),
            "Unit": i.unit,
            "Expiration": str(i.expiration_date or "Not set"),
            "Purchased": str(i.purchase_date or "Not set"),
        }
        for i in lots
    ]


def choose_food(api: APIClient, workspace: dict, prefix: str):
    with st.form(f"{prefix}_search_form"):
        query = st.text_input("Search existing foods", key=f"{prefix}_query")
        submitted = st.form_submit_button("Search foods")
    if submitted:
        with st.spinner("Searching foods…"):
            found = api.search_foods(query.strip())
        workspace["search"][prefix] = found
        workspace["foods"].update({food.id: food for food in found})
    results = workspace["search"].get(prefix, [])
    if not results:
        st.info("Search the food catalog to select an existing food. No matching foods yet.")
        return None
    return st.selectbox(
        "Food",
        [f.id for f in results],
        format_func=food_labels(results).get,
        key=f"{prefix}_food",
    )


def inventory(snapshot: dict, workspace: dict, prefix: str) -> None:
    query = st.text_input("Filter by food name", key=f"{prefix}_query")
    locations = labels(snapshot["locations"], context=lambda r: humanize(r.location_type))
    location = st.selectbox(
        "Location filter",
        [None, *locations],
        format_func=lambda value: locations.get(value, "All locations"),
        key=f"{prefix}_location",
    )
    view = st.selectbox("Inventory view", FILTERS, key=f"{prefix}_view")
    rows = inventory_rows(
        filtered_lots(snapshot, workspace["foods"], query, location, view),
        snapshot,
        workspace["foods"],
    )
    if rows:
        st.dataframe(
            rows,
            column_order=[
                "Food",
                "Quantity",
                "Unit",
                "Location",
                "Status",
                "Expiration alert",
                "Expiration",
                "Purchased",
            ],
            hide_index=True,
            use_container_width=True,
        )
        technical_details(
            inventory_records="\n".join(
                f"{item.id}: version {item.version}" for item in snapshot["items"]
            )
        )
        st.caption(
            "Expiration alerts and low-stock membership come from the API. Refresh for current stock."
        )
    else:
        st.info("No inventory matches these filters. Clear filters or add a pantry item.")


def locations_page(api: APIClient, home, snapshot: dict, workspace: dict, prefix: str):
    locations = snapshot["locations"]
    if locations:
        for location in locations:
            with st.container(border=True):
                st.subheader(location.name)
                badge(humanize(location.location_type))
    else:
        st.info("No storage locations yet. Create one below.")
    with st.form(f"{prefix}_create"):
        name = st.text_input("Location name", max_chars=100)
        kind = st.selectbox(
            "Storage type",
            ["pantry", "refrigerator", "freezer", "cabinet", "custom"],
            format_func=humanize,
        )
        submitted = st.form_submit_button("Create location")
    if submitted:
        api.create_pantry_location(home, LocationInput(name=name.strip(), location_type=kind))
        finish(workspace, f"Created {name.strip()}.")
        st.rerun()
    if locations:
        with st.expander("Delete an empty location"):
            selected = st.selectbox(
                "Location to delete",
                [r.id for r in locations],
                format_func=labels(locations, context=lambda r: humanize(r.location_type)).get,
                key=f"{prefix}_delete",
            )
            confirm = st.checkbox(
                "Confirm deletion of this empty location", key=f"{prefix}_{selected}_confirm"
            )
            if st.button("Delete location", disabled=not confirm):
                api.delete_pantry_location(home, selected)
                finish(workspace, "Storage location deleted.")
                st.rerun()
            st.caption(
                "Only empty locations can be deleted. The API checks whether lots still reference the location."
            )


def add_item(api: APIClient, home, snapshot: dict, workspace: dict, prefix: str):
    steps(("Choose food", "Choose storage", "Add amount & dates"))
    if not snapshot["locations"]:
        st.info("Create a storage location in Locations before adding inventory.")
        return
    food = choose_food(api, workspace, prefix)
    if food is None:
        return
    locations = labels(snapshot["locations"], context=lambda r: humanize(r.location_type))
    with st.form(f"{prefix}_add"):
        location = st.selectbox("Store in", list(locations), format_func=locations.get)
        quantity = st.text_input(
            "Quantity", "1", help="Positive decimal, up to three decimal places."
        )
        unit = st.selectbox("Unit", UNITS, format_func=unit_label)
        expiration = st.date_input("Expiration date (optional)", value=None)
        purchase = st.date_input("Purchase date (optional)", value=None)
        submitted = st.form_submit_button("Add pantry item")
    st.caption(
        "If saving times out, refresh inventory and check whether this food was added before trying again."
    )
    if submitted:
        data = PantryItemInput(
            food_id=food,
            location_id=location,
            quantity=quantity,
            unit=unit,
            expiration_date=expiration,
            purchase_date=purchase,
        )
        api.create_pantry_item(home, data)
        finish(workspace, f"Added {workspace['foods'][food].name} to {locations[location]}.")
        st.rerun()


def new_action(operation: str, item: PantryLot) -> dict:
    return {
        "operation": operation,
        "item": item,
        "key": uuid4().hex,
        "payload": None,
        "error": None,
    }


def execute_action(api: APIClient, home, workspace: dict) -> None:
    action = workspace["action"]
    operation, payload = action["operation"], action["payload"]
    try:
        if operation == "Increase quantity":
            api.adjust_pantry_item(home, action["item"].id, payload)
        elif operation == "Discard":
            api.discard_pantry_item(home, action["item"].id, payload)
        elif operation == "Consume (FEFO)":
            api.consume_pantry(home, payload)
        else:
            api.transfer_pantry(home, payload)
    except APIError as error:
        action["error"] = error
        raise
    finish(
        workspace,
        f"Updated {workspace['foods'][action['item'].food_id].name}. Inventory refreshed.",
    )
    st.rerun()


def actions(
    api: APIClient, home, snapshot: dict, workspace: dict, prefix: str, show_error: Callable
):
    action = workspace["action"]
    if action is None:
        if not snapshot["items"]:
            st.info("Add inventory before starting an action.")
            return
        selected = st.selectbox(
            "Food package",
            [r.id for r in snapshot["items"]],
            format_func=labels(
                snapshot["items"],
                name=lambda r: workspace["foods"][r.food_id].name,
                context=lambda r: (
                    f"{next((loc.name for loc in snapshot['locations'] if loc.id == r.location_id), 'Unknown location')} · {r.quantity} {r.unit} · {humanize(r.status)} · Best before {r.expiration_date or 'not set'}"
                ),
            ).get,
            key=f"{prefix}_lot",
        )
        operation = st.selectbox(
            "Action",
            ["Increase quantity", "Consume (FEFO)", "Transfer whole lot", "Discard"],
            format_func=lambda value: (
                "Use food (earliest expiry first)" if value == "Consume (FEFO)" else value
            ),
            key=f"{prefix}_operation",
        )
        if st.button("Start action"):
            workspace["action"] = new_action(
                operation, next(r for r in snapshot["items"] if r.id == selected)
            )
            st.rerun()
        return
    item, operation = action["item"], action["operation"]
    st.subheader("Use food" if operation == "Consume (FEFO)" else operation)
    st.write(f"{workspace['foods'][item.food_id].name} · {item.quantity} {item.unit}")
    technical_details(pantry_lot_ID=item.id, version=item.version)
    if st.button("Reset action with current stock"):
        workspace.update(action=None, snapshot=None, load_requested=True)
        st.rerun()
    if action["payload"] is not None:
        st.info(
            "Your submitted change is saved for a safe retry. Check refreshed inventory before resetting it to start a different change."
        )
        if action["error"]:
            show_error(action["error"])
        if st.button("Retry saved action"):
            execute_action(api, home, workspace)
        return
    if operation == "Consume (FEFO)":
        st.info(
            "Food is used from the earliest-expiring stock first, across all your locations. The chosen package identifies the food, not the only package that will be used."
        )
    elif operation == "Transfer whole lot":
        st.info(
            "The existing API transfers the entire lot to another location. Partial transfers are unavailable."
        )
    elif operation == "Discard":
        st.warning(
            "The API marks the entire lot discarded, even when discarding only part of its quantity. Any remainder becomes unusable."
        )
    else:
        st.caption("Adjustment increases quantity. Use Consume for a reduction in usable stock.")
    with st.form(f"{prefix}_{action['key']}_action"):
        if operation == "Transfer whole lot":
            targets = {r.id: r.name for r in snapshot["locations"] if r.id != item.location_id}
            target = st.selectbox("Destination location", list(targets), format_func=targets.get)
            quantity, unit, reason = None, None, None
        else:
            quantity = st.text_input(
                "Quantity to discard" if operation == "Discard" else "Quantity change", "1"
            )
            options = list(dict.fromkeys([item.unit, *UNITS]))
            unit = st.selectbox("Action unit", options, format_func=unit_label)
            reason = st.text_input("Reason (optional)", max_chars=300)
            target = None
        confirm = st.checkbox("Confirm discard of this lot") if operation == "Discard" else True
        submitted = st.form_submit_button("Submit action")
    if submitted:
        if not confirm:
            st.error("Confirm discard before submitting.")
            return
        if operation == "Transfer whole lot":
            if target is None:
                st.error("Create another storage location before transferring.")
                return
            payload = TransferInput(
                item_id=item.id,
                target_location_id=target,
                version=item.version,
                idempotency_key=action["key"],
            )
        elif operation == "Consume (FEFO)":
            payload = ConsumeInput(
                food_id=item.food_id,
                quantity=quantity,
                unit=unit,
                reason=reason or None,
                idempotency_key=action["key"],
            )
        else:
            payload = AdjustmentInput(
                quantity_change=quantity,
                unit=unit,
                reason=reason or None,
                version=item.version,
                idempotency_key=action["key"],
            )
        action["payload"] = payload
        execute_action(api, home, workspace)


def stock_rules(api: APIClient, home, snapshot: dict, workspace: dict, prefix: str):
    foods = workspace["foods"]
    low = {r.food_id for r in snapshot["low_stock"]}
    if snapshot["rules"]:
        st.dataframe(
            [
                {
                    "Food": foods[r.food_id].name,
                    "Threshold": f"{r.threshold_quantity} {r.threshold_unit}",
                    "Reorder": f"{r.preferred_reorder_quantity} {r.preferred_reorder_unit}",
                    "Stock status": "Low stock" if r.food_id in low else "Not low",
                }
                for r in snapshot["rules"]
            ],
            hide_index=True,
        )
    else:
        st.info("No low-stock rules yet. Select a food to set a threshold.")
    st.caption("Low-stock status is calculated by the API, using compatible quantities.")
    food = choose_food(api, workspace, prefix)
    if food is None:
        return
    rule = next((r for r in snapshot["rules"] if r.food_id == food), None)
    with st.form(f"{prefix}_{food}_rule"):
        threshold = st.text_input(
            "Low-stock threshold", str(rule.threshold_quantity) if rule else "1"
        )
        threshold_units = list(dict.fromkeys([rule.threshold_unit, *UNITS])) if rule else UNITS
        threshold_unit = st.selectbox("Threshold unit", threshold_units, format_func=unit_label)
        reorder = st.text_input(
            "Preferred reorder quantity", str(rule.preferred_reorder_quantity) if rule else "1"
        )
        reorder_units = (
            list(dict.fromkeys([rule.preferred_reorder_unit, *UNITS])) if rule else UNITS
        )
        reorder_unit = st.selectbox("Reorder unit", reorder_units, format_func=unit_label)
        submitted = st.form_submit_button("Save stock rule")
    if submitted:
        api.save_pantry_stock_rule(
            home,
            food,
            StockRuleInput(
                threshold_quantity=threshold,
                threshold_unit=threshold_unit,
                preferred_reorder_quantity=reorder,
                preferred_reorder_unit=reorder_unit,
            ),
        )
        finish(workspace, f"Saved restock reminder for {foods[food].name}.")
        st.rerun()


def render_pantry(api: APIClient, household: Household, show_error: Callable) -> None:
    st.write("See what you have, where it is stored, and what needs using or replacing.")
    workspace = st.session_state.setdefault(f"pantry_{household.id}", new_workspace())
    prefix = f"pantry_{household.id}"
    intent = st.session_state.get("intent")
    if intent in ("Add pantry item", "Expiring items", "Low-stock items"):
        st.session_state[f"{prefix}_section"] = (
            "Add item" if intent == "Add pantry item" else "Inventory"
        )
        if intent != "Add pantry item":
            st.session_state[f"{prefix}_inventory_query"] = ""
            st.session_state[f"{prefix}_inventory_location"] = None
            st.session_state[f"{prefix}_inventory_view"] = (
                "Expiring soon" if intent == "Expiring items" else "Low stock"
            )
        st.session_state["intent"] = ""
    if workspace["notice"]:
        st.success(workspace["notice"])
        workspace["notice"] = None
    if st.button("Refresh pantry"):
        workspace["snapshot"] = None
        workspace["load_requested"] = True
    st.caption("Refresh checks freshness and marks overdue lots expired. Your last loaded snapshot stays available while you browse.")
    if workspace["snapshot"] is None and not workspace.pop("load_requested", False):
        st.info("Refresh pantry to load your inventory and check freshness. Opening this page does not change stock.")
        return
    try:
        with st.spinner("Loading pantry…"):
            snapshot = load_snapshot(api, household.id, workspace)
        counts = snapshot["summary"]
        for column, label, value in zip(
            st.columns(4),
            ["Active inventory", "Expiring soon", "Expired", "Low stock"],
            [
                counts.active_items,
                len(snapshot["expiring"]),
                counts.expired_items,
                len(snapshot["low_stock"]),
            ],
            strict=True,
        ):
            column.metric(label, value)
        section = st.radio("Pantry section", SECTIONS, horizontal=True, key=f"{prefix}_section")
        st.caption(
            {
                "Inventory": "Filter your food by location or freshness, then use Actions to change stock.",
                "Add item": "Choose a food and storage location, then enter its amount; dates are optional.",
                "Locations": "Create named storage spaces such as Kitchen cupboard or Garage freezer.",
                "Actions": "Choose the food and action, review the amount, then confirm your change.",
                "Low-stock rules": "Choose a food and the amount below which you want a reminder to restock.",
                "History": "Review past stock changes when history becomes available.",
            }[section]
        )
        form_prefix = f"{prefix}_{workspace['epoch']}_{section}"
        if section == "Inventory":
            inventory(snapshot, workspace, f"{prefix}_inventory")
        elif section == "Locations":
            locations_page(api, household.id, snapshot, workspace, form_prefix)
        elif section == "Add item":
            add_item(api, household.id, snapshot, workspace, form_prefix)
        elif section == "Actions":
            actions(api, household.id, snapshot, workspace, form_prefix, show_error)
        elif section == "Low-stock rules":
            stock_rules(api, household.id, snapshot, workspace, form_prefix)
        else:
            st.info(
                "Pantry history is not available through the current API. Inventory actions record append-only transactions, but there is no endpoint to read them yet."
            )
            st.caption(
                "Transaction type, quantity, unit, timestamp, and item/location references will appear here when a read endpoint is available. This page does not infer history from current stock."
            )
    except ValidationError as error:
        validation_errors(error)
    except APIError as error:
        show_error(error)
        if error.code == "stale_inventory_version":
            st.info(
                "Refresh pantry to load current stock, then reset the action before making a new change."
            )
        elif error.code == "duplicate_idempotency_key":
            st.info(
                "This action key was already used. Refresh inventory to check the result before starting a new action."
            )
        elif error.code == "pantry_location_not_empty":
            st.info(
                "This location still has inventory records. Transfer usable lots or keep the location for its existing records."
            )
