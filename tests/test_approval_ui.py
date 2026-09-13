from unittest.mock import MagicMock

from streamlit.testing.v1 import AppTest
from test_approval import approve, execute, preview, propose  # noqa: F401

from nourish_nest.api_client import APIError
from nourish_nest.approval_contracts import ApprovalPreview, CriticResult
from nourish_nest.approval_ui import new_workspace
from nourish_nest.evals.multi_agent_fixtures import HOME


def button(ui, label):
    return next(b for b in ui.button if b.label == label)


def app(api):
    ui = AppTest.from_string('''
import streamlit as st
from types import SimpleNamespace
from uuid import UUID
from nourish_nest.approval_ui import render_approval
if not st.session_state.get("hide_workflow"):
    render_approval(st.session_state["api"], SimpleNamespace(id=UUID(int=501)), lambda e: st.error(e.message))
''', default_timeout=20)
    ui.session_state["api"] = api
    return ui.run()


def fixture_api(engine, result):
    api = MagicMock()
    api.members.return_value = []
    api.multi_agent_preview.return_value = ApprovalPreview.model_validate(result.model_dump())
    api.critic_review.return_value = CriticResult(decision="pass_with_warnings", warnings=["stored_allergen_data"])
    p = propose(engine, result.run_id)
    approved = approve(engine, p)
    api.create_proposal.return_value = p
    api.approval_decision.return_value = approved
    api.execute_proposal.return_value = execute(engine, approved)
    return api


def prepare(ui):
    ui.text_area[0].input("Plan five vegetarian dinners for two adults and show grocery shortages.").run()
    button(ui, "Build multi-agent preview").click().run()
    button(ui, "Prepare action proposal").click().run()
    assert not ui.exception


def test_approval_flow_and_reruns(preview):  # noqa: F811 - imported pytest fixture
    engine, result = preview
    api = fixture_api(engine, result)
    ui = app(api)
    prepare(ui)
    assert button(ui, "Approve").disabled and button(ui, "Create grocery list").disabled
    api.execute_proposal.assert_not_called()
    ui.checkbox[0].check().run()
    button(ui, "Approve").click().run()
    assert not button(ui, "Create grocery list").disabled
    api.execute_proposal.assert_not_called()
    ui.run()
    button(ui, "Create grocery list").click().run()
    assert not ui.exception and button(ui, "Create grocery list").disabled
    assert button(ui, "Cancel proposal").disabled
    ui.run()
    message = ui.text_area[0].value
    saved = ui.session_state[f"approval_{HOME}"]
    saved.pop("members")
    saved.pop("message")
    ui.session_state["hide_workflow"] = True
    ui.run()
    ui.session_state["hide_workflow"] = False
    ui.run()
    assert ui.text_area[0].value == message
    api.create_proposal.assert_called_once()
    api.approval_decision.assert_called_once()
    api.execute_proposal.assert_called_once()
    ordinary = " ".join(str(row.value) for kind in (ui.markdown, ui.caption, ui.info, ui.warning, ui.success) for row in kind)
    assert str(HOME) not in ordinary and str(result.run_id) not in ordinary
    assert all(not e.proto.expanded for e in ui.expander if e.label == "Technical details")
    button(ui, "Open Grocery Lists").click().run()
    assert ui.session_state["page"] == "Grocery Lists"


def test_retry_preserves_key(preview):  # noqa: F811 - imported pytest fixture
    engine, result = preview
    api = fixture_api(engine, result)
    success = api.execute_proposal.return_value
    api.execute_proposal.side_effect = [APIError("timeout", "Try again"), success]
    ui = app(api)
    prepare(ui)
    ui.checkbox[0].check().run()
    button(ui, "Approve").click().run()
    button(ui, "Create grocery list").click().run()
    assert not ui.exception and not button(ui, "Create grocery list").disabled
    button(ui, "Create grocery list").click().run()
    calls = api.execute_proposal.call_args_list
    assert len(calls) == 2 and calls[0].args == calls[1].args


def test_blocked_critic_stops_ui(preview):  # noqa: F811 - imported pytest fixture
    engine, result = preview
    api = fixture_api(engine, result)
    api.critic_review.return_value = CriticResult(decision="block", blocking_issues=["allergy_conflict"])
    ui = app(api)
    ui.text_area[0].input("Plan meals").run()
    button(ui, "Build multi-agent preview").click().run()
    assert not any(b.label == "Prepare action proposal" for b in ui.button)
    api.create_proposal.assert_not_called()
    api.execute_proposal.assert_not_called()


def test_new_workspace_isolation():
    assert new_workspace()["key"] != new_workspace()["key"]
