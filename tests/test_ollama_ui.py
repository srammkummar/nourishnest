import httpx
import pytest
import test_assistant_ui as assistant_tests
from test_assistant_ui import (  # noqa: F401
    HOME,
    RESULT,
    api,
    open_ui,
    submit,
)

from nourish_nest.api_client import APIClient, APIResponseError
from nourish_nest.assistant_client_models import AssistantInput

assistant_api = assistant_tests.assistant_api


def test_local_mode_model_notice_and_unavailable_recovery(assistant_api, monkeypatch):
    monkeypatch.setenv("APP_AI_PROVIDER", "ollama")
    monkeypatch.setenv("APP_AI_MODEL", "local-test:1b")
    assistant_api.assistant_preview.side_effect = APIResponseError(
        "assistant_provider_unavailable",
        "The meal assistant is unavailable.",
        "local-trace",
        503,
    )
    ui = open_ui()
    assert any("Local Ollama mode" in row.value for row in ui.info)
    assert any("local-test:1b" in row.value for row in ui.caption)
    submit(ui, "Plan 1 vegan dinner for 2 people")
    assert ui.text_area[0].value == "Plan 1 vegan dinner for 2 people"
    assert any("Start local Ollama" in row.value for row in ui.info)
    assert any("local-trace" in row.value for row in ui.text)
    assert all(not row.proto.expanded for row in ui.expander if row.label == "Technical details")
    assistant_api.assistant_preview.side_effect = None
    assistant_api.assistant_preview.return_value = RESULT.model_copy(
        update={"model_version": "ollama:local-test:1b@ollama-meal-intent-v1"}
    )
    submit(ui, "Plan 1 vegan dinner for 2 people")
    assert not ui.exception
    assert assistant_api.assistant_preview.call_count == 2


def test_local_client_waits_for_model_without_repeating_generation(monkeypatch):
    monkeypatch.setenv("APP_AI_PROVIDER", "ollama")
    monkeypatch.setenv("APP_AI_TIMEOUT_SECONDS", "60")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            503,
            json={
                "code": "assistant_provider_unavailable",
                "message": "Unavailable",
                "request_id": "trace",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = APIClient(client=transport)
        with pytest.raises(APIResponseError):
            client.assistant_preview(HOME.id, AssistantInput(user_message="Plan dinner"))
    assert len(calls) == 1
    assert calls[0].extensions["timeout"]["read"] == 65
