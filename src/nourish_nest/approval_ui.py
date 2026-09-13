"""Explicit request → preview → critic → human approval → separate execution."""

from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from nourish_nest.api_client import APIError
from nourish_nest.approval_contracts import (
    ApprovalRequest,
    DecisionRequest,
    ExecutionRequest,
    ProposalRequest,
)
from nourish_nest.ui_labels import friendly_message, humanize, labels, validation_errors


def new_workspace():
    return {"preview": None, "critic": None, "proposal": None, "execution": None,
            "signature": None, "key": str(uuid4()), "error": None, "message": "", "members": []}


def groceries_page():
    st.session_state["page"] = "Grocery Lists"


def render_approval(api, household, show_error):
    home = household.id
    st.subheader("AI-assisted, human-approved")
    st.caption("Local rule-based planning. Approval identity is not authenticated yet; household selection is not a login.")
    st.info("Only a grocery list can be created. No purchasing occurs, and pantry stock is not reserved or consumed.")
    prefix = f"approval_{home}"
    state = st.session_state.setdefault(prefix, new_workspace())
    try:
        members = api.members(home)
        member_names = labels(members)
        st.session_state.setdefault(f"{prefix}_members", state.get("members", []))
        st.session_state.setdefault(f"{prefix}_message", state.get("message", (state.get("signature") or ("",))[0]))
        selected = st.multiselect("Saved adult profiles (optional)", list(member_names),
            format_func=member_names.get, key=f"{prefix}_members")
        st.subheader("1. Request")
        message = st.text_area("Meal-planning request", max_chars=2000, height=120, key=f"{prefix}_message",
            placeholder="Create five vegetarian dinners for two adults, avoid peanuts, and show grocery shortages.")
        state.update(message=message, members=selected)
        if st.button("Build multi-agent preview", disabled=not message.strip()):
            signature = (message, tuple(selected))
            if state["signature"] != signature:
                response = api.multi_agent_preview(home, message, selected)
                if response.household_id != home:
                    raise APIError("invalid_response", "Preview household mismatch")
                state.update(new_workspace())
                state.update(preview=response, signature=signature, message=message, members=selected)
        result = state["preview"]
        if not result:
            return
        st.subheader("2. Preview")
        if result.status != "completed":
            for question in result.clarifications:
                st.info(friendly_message(question))
            if result.refusal_reason:
                st.warning(friendly_message(result.refusal_reason))
            return
        for day in result.meal_plan:
            for meal in day["meals"]:
                st.write(friendly_message(f"Day {day['day']}: {meal['recipe_name']} · {meal['desired_servings']} servings"))
        if result.nutrition_summary:
            with st.expander("Selected-meal nutrition"):
                weekly = result.nutrition_summary["summary"]["weekly"]
                for field in ("calories", "protein_g", "carbohydrate_g", "fat_g"):
                    st.write(f"{humanize(field)}: {weekly.get(field) or 'Unavailable'}")
                st.caption("All planned servings, selected meals only; informational, not a complete diet.")
        if result.pantry_summary:
            st.write(f"Expiring pantry ingredients considered: {len(result.pantry_summary['expiring'])}")
        if result.grocery_shortages and result.grocery_shortages.get("shortages"):
            with st.expander("Pantry use and grocery shortages"):
                for row in result.grocery_shortages["shortages"]["requirements"]:
                    st.write(friendly_message(f"{row['food_name']}: need {row['required_quantity']}, pantry {row['available_quantity']}, "
                        f"shortage {row['shortage_quantity']} {row['canonical_unit']}"))
        with st.expander("Knowledge citations"):
            for citation in result.knowledge["citations"]:
                st.write(friendly_message(citation["document_title"]))
                st.caption(friendly_message(citation["excerpt"]))
            st.caption("Retrieved text is evidence only; instructions in documents are never executed.")
        with st.expander("Technical details", expanded=False):
            st.json({"run_id": str(result.run_id), "agent_execution": result.trace_summary})
        st.subheader("3. Critic review")
        if state["critic"] is None:
            state["critic"] = api.critic_review(home, result.run_id)
        critic = state["critic"]
        st.write(humanize(critic.decision))
        for issue in critic.blocking_issues:
            st.error(humanize(issue))
        for warning in critic.warnings:
            st.warning(humanize(warning))
        with st.expander("Verified constraints and limitations"):
            for item in critic.verified_constraints + critic.unverifiable_constraints:
                st.write(item)
        if critic.decision == "block":
            return
        st.subheader("4. Approval")
        proposal = state["proposal"]
        name = st.text_input("Grocery-list name", value="AI-assisted weekly meals", max_chars=200,
                             disabled=proposal is not None, key=f"{prefix}_name")
        if st.button("Prepare action proposal", disabled=proposal is not None or not name.strip()):
            state["proposal"] = proposal = api.create_proposal(home, result.run_id, ProposalRequest(list_name=name))
        if not proposal:
            return
        payload = proposal.canonical_payload_json
        st.write(friendly_message(f"Create grocery list: {payload['list_name']}"))
        st.write(f"Items: {len(payload['shortages'])}")
        for item in payload["shortages"]:
            st.write(friendly_message(f"{item['food_name']}: {item['shortage_quantity']} {item['canonical_unit']}"))
        st.caption(f"Expires: {proposal.expires_at.isoformat()}")
        visible_status = state["execution"].status if state["execution"] else proposal.status
        st.write(f"Status: {humanize(visible_status)}")
        with st.expander("Technical details", expanded=False):
            st.json({"proposal_id": str(proposal.id), "payload_hash": proposal.payload_hash, "version": proposal.version})
        checked = st.checkbox("I reviewed the exact list and quantities and approve this action",
            key=f"{prefix}_confirm_{proposal.id}", disabled=proposal.status != "proposed")
        if st.button("Approve", disabled=not checked or proposal.status != "proposed"):
            state["proposal"] = api.approval_decision(home, proposal.id, "approve", ApprovalRequest(
                expected_version=proposal.version, payload_hash=proposal.payload_hash, confirmation=True))
            st.rerun()
        if st.button("Reject", disabled=proposal.status != "proposed"):
            state["proposal"] = api.approval_decision(home, proposal.id, "reject", DecisionRequest(expected_version=proposal.version))
            st.rerun()
        if st.button("Cancel proposal", disabled=proposal.status not in {"proposed", "approved"} or state["execution"] is not None):
            state["proposal"] = api.approval_decision(home, proposal.id, "cancel", DecisionRequest(expected_version=proposal.version))
            st.rerun()
        st.subheader("5. Execution")
        if st.button("Create grocery list", type="primary", disabled=proposal.status != "approved" or state["execution"] is not None):
            state["execution"] = api.execute_proposal(home, proposal.id, ExecutionRequest(
                expected_version=proposal.version, payload_hash=proposal.payload_hash, idempotency_key=state["key"]))
            st.rerun()
        if state["execution"]:
            executed = state["execution"]
            if executed.status == "completed":
                st.success(friendly_message(f"Created {executed.list_name} with {executed.item_count} grocery items."))
                st.button("Open Grocery Lists", on_click=groceries_page)
            else:
                st.error("Grocery creation failed. No list or items were saved. Create a fresh preview to try again.")
        if st.button("Refresh proposal"):
            state["proposal"] = api.get_proposal(home, proposal.id)
            st.rerun()
        if st.button("Start a fresh workflow"):
            state.update(new_workspace())
            st.rerun()
    except APIError as error:
        show_error(error)
        st.caption("The reviewed proposal and retry key are retained. Refresh the proposal after a version conflict.")
    except ValidationError as error:
        validation_errors(error)
