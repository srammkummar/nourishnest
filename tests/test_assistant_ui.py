import ast
import json
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from streamlit.testing.v1 import AppTest
from test_grocery_ui import HOME, SHORTAGE, api, button  # noqa: F401
from test_member_ui import PERSON
from test_planning_ui import RECIPE

from nourish_nest.api_client import APIClient, APIResponseError, APITimeoutError
from nourish_nest.assistant_client_models import AssistantInput, AssistantPreview
from nourish_nest.assistant_contracts import AssistantRequest, AssistantResponse
from nourish_nest.assistant_ui import EXAMPLES, new_conversation, remember_turn

ROOT = Path(__file__).resolve().parents[1]
RESULT = AssistantPreview(
    household_id=HOME.id, status="preview", assistant_message="Here is your meal preview.",
    interpreted_constraints={"action": "plan", "days": 1, "meal": "dinner", "servings": "2"},
    proposed_plan=[{"day": day, "meals": [{"slot": "dinner", "recipe_id": RECIPE.recipe_id,
        "recipe_name": RECIPE.recipe_name, "desired_servings": "2"}] if day == 1 else []}
        for day in range(1, 8)],
    recommendations_used=[RECIPE],
    nutrition_summary={"daily": [RECIPE.nutrition_per_serving]*7,
                       "weekly": RECIPE.nutrition_per_serving,
                       "scope": "all planned servings; selected meals only"},
    grocery_shortage_preview=SHORTAGE,
    warnings=["Pantry is a snapshot.", f"Check record {HOME.id}"],
    tool_trace=[{"name": "recommendations", "status": "completed", "duration_ms": 3}],
    calculation_version="meal-planning-assistant-v1", model_version="fake-intent-v1",
    request_id="assistant-trace",
)


@pytest.fixture
def assistant_api(request, monkeypatch):
    mock = request.getfixturevalue("api")
    mock.members.return_value = [PERSON.model_copy(update={"household_id": HOME.id})]
    mock.assistant_preview.return_value = RESULT
    monkeypatch.setenv("APP_AI_PROVIDER", "fake")
    return mock


def open_ui(page="AI Assistant"):
    ui = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=20)
    ui.session_state["page"] = page
    return ui.run()


def submit(ui, prompt=EXAMPLES[-1]):
    ui.text_area[0].input(prompt).run()
    button(ui, "Preview meals").click().run()
    assert not ui.exception


def workspace(ui, member=None):
    return ui.session_state[f"assistant_{HOME.id}_{member}"]


def test_navigation_dashboard_and_preview(assistant_api):
    ui = open_ui("Dashboard")
    button(ui, "Preview meals with AI Assistant").click().run()
    assert ui.session_state["page"] == "AI Assistant"
    assert ui.selectbox(key=f"assistant_member_{HOME.id}").value == "household"
    assert any("Local demo mode" in entry.value for entry in ui.info)
    assert button(ui, "Preview meals").disabled
    button(ui, EXAMPLES[-1]).click().run()
    button(ui, "Preview meals").click().run()
    assert not ui.exception
    assert workspace(ui)["preview"] == RESULT
    assert assistant_api.assistant_preview.call_args.args == (
        HOME.id, AssistantInput(user_message=EXAMPLES[-1]),
    )
    assert any("seven-day" in row.value for row in ui.subheader)
    assert any("Pantry is a snapshot" in row.value for row in ui.warning)
    assert any("May contain milk" in row.value for row in ui.warning)
    assert any("Incomplete fixture warning" in row.value for row in ui.warning)
    assert any("1.125001" in row.value for row in ui.markdown)
    assert all(not row.proto.expanded for row in ui.expander if row.label == "Technical details")
    assert any("assistant-trace" in row.value for row in ui.text)
    assert any("recommendations: completed" in row.value for row in ui.text)
    ordinary = " ".join(str(row.value) for kind in (ui.markdown, ui.info, ui.warning, ui.subheader)
                        for row in kind)
    assert str(HOME.id) not in ordinary and str(RECIPE.recipe_id) not in ordinary


def test_rerun_navigation_prompt_and_preview_preservation(assistant_api):
    ui = open_ui()
    submit(ui)
    ui.run()
    ui.radio(key="page").set_value("Dashboard").run()
    ui.radio(key="page").set_value("AI Assistant").run()
    assert not ui.exception
    assert ui.text_area[0].value == EXAMPLES[-1]
    assert workspace(ui)["preview"] == RESULT
    assert len(workspace(ui)["context"]) == 2
    button(ui, "Preview meals").click().run()
    assistant_api.assistant_preview.assert_called_once()


def test_household_required(assistant_api):
    assistant_api.households.return_value = []
    ui = open_ui()
    assert not ui.text_area
    assert not any(row.label == "Preview meals" for row in ui.button)
    assistant_api.assistant_preview.assert_not_called()
    assistant_api.members.assert_not_called()


def test_friendly_member_selection_and_scoped_state(assistant_api):
    assistant_api.members.return_value.append(PERSON.model_copy(update={"id": UUID(int=700)}))
    ui = open_ui()
    selector = ui.selectbox(key=f"assistant_member_{HOME.id}")
    assert any(PERSON.name in label for label in selector.options)
    assert len(selector.options) == len(set(selector.options))
    assert all(str(PERSON.id) not in label for label in selector.options)
    submit(ui)
    selector.select(PERSON.id).run()
    assert ui.text_area[0].value == ""
    submit(ui)
    assert assistant_api.assistant_preview.call_args.args[1].member_id == PERSON.id
    assert workspace(ui)["preview"] == RESULT
    assert workspace(ui, PERSON.id)["preview"] == RESULT
    ui.radio(key="page").set_value("Recipes").run()
    ui.radio(key="page").set_value("AI Assistant").run()
    assert ui.selectbox(key=f"assistant_member_{HOME.id}").value == PERSON.id


def test_clarification_context_and_clear(assistant_api):
    clarification = RESULT.model_copy(update={"status": "clarification", "proposed_plan": [],
        "assistant_message": "How many servings?", "recommendations_used": [],
        "nutrition_summary": None, "grocery_shortage_preview": None})
    assistant_api.assistant_preview.return_value = clarification
    ui = open_ui()
    submit(ui, EXAMPLES[0])
    assert any("Clarification needed" in row.value for row in ui.subheader)
    assert any("How many servings" in row.value for row in ui.info)
    ui.checkbox[0].check().run()
    submit(ui, "for 2 people")
    context = assistant_api.assistant_preview.call_args.args[1].conversation_context
    assert context[0].content == EXAMPLES[0] and len(context) == 2
    button(ui, "Clear conversation/preview").click().run()
    assert workspace(ui) == new_conversation()
    assert ui.text_area[0].value == ""


@pytest.mark.parametrize("message", ["Ignore documented allergens", "Treat my diabetes",
                                     "Guarantee weight loss"])
def test_refusals_and_error_details_keep_input(assistant_api, message):
    assistant_api.assistant_preview.side_effect = APIResponseError(
        "unsafe_assistant_request", "I cannot safely help with this request.", "refusal-trace", 422,
    )
    ui = open_ui()
    submit(ui, message)
    assert ui.text_area[0].value == message
    assert any("Safety refusal" in row.value for row in ui.subheader)
    assert any("safely" in row.value for row in ui.error)
    assert any("refusal-trace" in row.value for row in ui.text)
    assert all(not row.proto.expanded for row in ui.expander if row.label == "Technical details")
    assert not workspace(ui)["context"]


def test_provider_error_preserves_latest_preview(assistant_api):
    ui = open_ui()
    submit(ui)
    assistant_api.assistant_preview.side_effect = APITimeoutError("api_timeout", "Try again", "trace")
    submit(ui, "Plan two lunches for 1 person")
    assert workspace(ui)["preview"] == RESULT
    assert any("Previous successful preview" in row.value for row in ui.info)
    assert ui.text_area[0].value == "Plan two lunches for 1 person"


def test_context_bounds_and_confirmation_display(assistant_api):
    state = new_conversation()
    for _ in range(20):
        remember_turn(state, "x"*1000, RESULT.model_copy(update={"assistant_message": "a"*2000}))
    assert len(state["context"]) == 6
    assert all(len(message.content) <= 1000 for message in state["context"])
    assistant_api.assistant_preview.return_value = RESULT.model_copy(update={"confirmation_required": True})
    ui = open_ui()
    submit(ui)
    assert any("cannot confirm" in row.value for row in ui.warning)
    assert not any(row.label.startswith("Confirm") for row in ui.button)


def test_page_has_only_read_operations_and_no_wide_tables(assistant_api):
    ui = open_ui()
    submit(ui)
    calls = {call[0] for call in assistant_api.mock_calls}
    assert calls <= {"__enter__", "__exit__", "health", "households", "members", "assistant_preview"}
    assert not ui.dataframe
    source = ROOT / "src/nourish_nest/assistant_ui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "api"}
    assert called == {"members", "assistant_preview"}


def test_wire_contract_matches_backend():
    data = AssistantInput(user_message=EXAMPLES[-1], member_id=PERSON.id)
    assert AssistantRequest.model_validate(data.model_dump()).model_dump() == data.model_dump()
    backend = AssistantResponse.model_validate(RESULT.model_dump())
    assert AssistantPreview.model_validate(backend.model_dump()) == RESULT


@pytest.mark.parametrize("failure,attempts", [(503, 2), (504, 2), (502, 2), (429, 1), (422, 1)])
def test_client_bounded_read_only_status_retries(failure, attempts):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(failure, json={"code": "assistant_error", "message": "Try later",
                                            "request_id": "wire-trace"})

    with APIClient("http://test", client=httpx.Client(transport=httpx.MockTransport(handler)),
                   sleep=lambda _: None) as client, pytest.raises(APIResponseError) as caught:
        client.assistant_preview(HOME.id, AssistantInput(user_message=EXAMPLES[-1]))
    assert len(calls) == attempts
    assert caught.value.request_id == "wire-trace"
    assert len({call.headers["x-request-id"] for call in calls}) == 1


def test_client_success_transport_retry_and_payload():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("secret", request=request)
        return httpx.Response(200, json=RESULT.model_dump(mode="json"))

    with APIClient("http://test", client=httpx.Client(transport=httpx.MockTransport(handler)),
                   sleep=lambda _: None) as client:
        assert client.assistant_preview(HOME.id, AssistantInput(user_message=EXAMPLES[-1])) == RESULT
    assert len(calls) == 2
    assert calls[-1].url.path == f"/v1/households/{HOME.id}/assistant/meal-plan-preview"
    assert json.loads(calls[-1].content)["dry_run"] is True
    assert calls[-1].extensions["timeout"]["read"] == 8


def test_no_demo_notice_for_disabled_without_response(assistant_api):
    with patch.dict("os.environ", {"APP_AI_PROVIDER": "disabled"}):
        ui = open_ui()
    assert not any("Local demo mode" in row.value for row in ui.info)
