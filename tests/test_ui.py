import ast
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from nourish_nest.api_client import (
    APIClient,
    APIProtocolError,
    APIResponseError,
    APITimeoutError,
    APIUnavailableError,
    Household,
    UISettings,
)
from nourish_nest.ui_state import remember_created_household, sync_household_selection

ROOT = Path(__file__).resolve().parents[1]
HOME = {"id": str(UUID(int=1)), "name": "Maple House", "timezone": "UTC", "currency": "USD"}


def make_client(handler):
    return APIClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )


def test_client_success_and_structured_error():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["name"] == "Maple House"
            return httpx.Response(201, json=HOME)
        return httpx.Response(
            404, json={"code": "not_found", "message": "Missing household", "request_id": "trace-1"}
        )

    api = make_client(handler)
    assert api.create_household(" Maple House ").id == UUID(HOME["id"])
    with pytest.raises(APIResponseError) as caught:
        api.households()
    assert (caught.value.code, caught.value.request_id, caught.value.status_code) == (
        "not_found",
        "trace-1",
        404,
    )
    assert requests[0].headers["x-request-id"]
    assert requests[0].extensions["timeout"]["read"] == 8


@pytest.mark.parametrize(
    "exception,expected",
    [(httpx.ReadTimeout, APITimeoutError), (httpx.ConnectError, APIUnavailableError)],
)
@pytest.mark.parametrize("mutation,attempts", [(False, 2), (True, 1)])
def test_transport_failures_and_retry_boundary(exception, expected, mutation, attempts):
    calls = []

    def handler(request):
        calls.append(request)
        raise exception("secret detail", request=request)

    api = make_client(handler)
    with pytest.raises(expected) as caught:
        api.create_household("Home") if mutation else api.health()
    assert len(calls) == attempts
    assert "secret" not in str(caught.value)
    assert caught.value.request_id


@pytest.mark.parametrize(
    "status,body,code",
    [
        (200, "not-json", "invalid_response"),
        (200, "{}", "invalid_response"),
        (302, "", "unexpected_status"),
        (500, "secret", "invalid_response"),
    ],
)
def test_invalid_responses(status, body, code):
    api = make_client(
        lambda _: httpx.Response(status, text=body, headers={"x-request-id": "trace"})
    )
    with pytest.raises(APIProtocolError) as caught:
        api.health()
    assert caught.value.code == code
    assert caught.value.request_id == "trace"
    assert "secret" not in str(caught.value)


def test_safe_get_status_retry_and_external_client_ownership():
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(503)
            if len(calls) == 1
            else httpx.Response(200, json={"status": "ok", "version": "test"})
        )

    api = make_client(handler)
    with api:
        assert api.health().version == "test"
    assert len(calls) == 2
    assert not api._client.is_closed


def test_config_rejects_credentials_without_exposing_them():
    with pytest.raises(APIProtocolError) as caught:
        APIClient("http://user:secret@localhost:8000")
    assert "secret" not in str(caught.value)


def test_api_url_configuration(monkeypatch):
    monkeypatch.delenv("APP_API_BASE_URL", raising=False)
    assert UISettings(_env_file=None).api_base_url == "http://127.0.0.1:8000"
    monkeypatch.setenv("APP_API_BASE_URL", "http://127.0.0.1:8001")
    assert UISettings(_env_file=None).api_base_url == "http://127.0.0.1:8001"


def test_session_selection():
    first = Household.model_validate(HOME)
    second = Household(id=UUID(int=2), name="Second")
    state = {}
    assert sync_household_selection(state, [first, second]) == str(first.id)
    state["household_id"] = str(second.id)
    assert sync_household_selection(state, [first, second]) == str(second.id)
    assert sync_household_selection(state, [first]) == str(first.id)
    remember_created_household(state, second)
    assert sync_household_selection(state, [first, second]) == str(second.id)
    assert sync_household_selection(state, []) is None


class Server:
    def __init__(self, homes=None, populated=False):
        self.homes = list(homes or [])
        self.populated = populated
        self.calls = []
        self.fail_path = None

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        if path == self.fail_path:
            return httpx.Response(
                503, json={"code": "unavailable", "message": "Try later", "request_id": "ui-trace"}
            )
        if path == "/health":
            data = {"status": "ok", "version": "test"}
        elif path == "/v1/households":
            if request.method == "POST":
                home = {**HOME, **json.loads(request.content)}
                self.homes.append(home)
                return httpx.Response(201, json=home)
            data = self.homes
        elif path.endswith("/summary"):
            data = {
                "active_items": 3 if self.populated else 0,
                "low_stock_food_ids": [HOME["id"]] if self.populated else [],
            }
        elif path.endswith("/grocery-lists"):
            data = (
                [{"id": HOME["id"], "status": status} for status in ("active", "draft")]
                if self.populated
                else []
            )
        else:
            data = [{"id": HOME["id"]}] if self.populated else []
        return httpx.Response(200, json=data)


def app(monkeypatch, server):
    monkeypatch.setattr("nourish_nest.streamlit_ui.create_api_client", lambda: make_client(server))
    return AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15).run()


def test_ui_empty_create_household_and_dashboard(monkeypatch):
    server = Server()
    ui = app(monkeypatch, server)
    assert not ui.exception
    assert any(x.value == "Welcome home" for x in ui.subheader)
    ui.text_input(key="new_household_name").input("Maple House")
    next(b for b in ui.button if b.label == "Create household").click().run()
    assert not ui.exception
    assert ui.session_state["household_id"] == HOME["id"]
    assert len(ui.metric) == 6
    assert all(m.value == "0" for m in ui.metric)
    assert any("no dashboard data" in x.value for x in ui.info)
    assert any("Created Maple House" in x.value for x in ui.success)
    assert sum(r.method == "POST" for r in server.calls) == 1


def test_ui_populated_selection_and_placeholder(monkeypatch):
    second = {**HOME, "id": str(UUID(int=2)), "name": "Second"}
    server = Server([HOME, second], populated=True)
    ui = app(monkeypatch, server)
    assert not ui.exception
    assert [m.value for m in ui.metric] == ["1", "1", "3", "1", "1", "1"]
    ui.selectbox(key="household_id").select(second["id"]).run()
    assert not ui.exception
    assert ui.session_state["household_id"] == second["id"]
    ui.run()
    assert ui.session_state["household_id"] == second["id"]
    ui.button(key="action_Pantry").click().run()
    assert not ui.exception
    assert ui.session_state["page"] == "Pantry"
    assert any("Phase 6B" in x.value for x in ui.info)


def test_ui_api_failure_and_request_id(monkeypatch):
    server = Server()
    server.fail_path = "/health"
    ui = app(monkeypatch, server)
    assert not ui.exception
    assert any("Try later" in e.value for e in ui.error)
    assert any("ui-trace" in t.value for t in ui.text)
    assert not ui.metric


def test_create_failure_has_no_automatic_retry(monkeypatch):
    server = Server()
    ui = app(monkeypatch, server)
    original = server.__call__

    def handler(request):
        if request.method == "POST":
            server.calls.append(request)
            return httpx.Response(
                422,
                json={
                    "code": "validation_error",
                    "message": "Invalid timezone",
                    "request_id": "create-trace",
                },
            )
        return original(request)

    monkeypatch.setattr("nourish_nest.streamlit_ui.create_api_client", lambda: make_client(handler))
    ui.text_input(key="new_household_name").input("Maple House")
    next(b for b in ui.button if b.label == "Create household").click().run()
    assert not ui.exception
    assert any("Invalid timezone" in x.value for x in ui.error)
    assert any("create-trace" in x.value for x in ui.text)
    assert sum(r.method == "POST" for r in server.calls) == 1
    assert not server.homes


def test_dashboard_failure_not_zero_and_pantries_not_retried(monkeypatch):
    server = Server([HOME])
    server.fail_path = f"/v1/households/{HOME['id']}/pantry/summary"
    ui = app(monkeypatch, server)
    assert not ui.exception
    assert not ui.metric
    assert sum(r.url.path == server.fail_path for r in server.calls) == 1


def test_ui_import_boundary():
    allowed = {"nourish_nest.api_client", "nourish_nest.ui_state", "nourish_nest.streamlit_ui"}
    for path in [
        ROOT / "streamlit_app.py",
        *(
            ROOT / "src" / "nourish_nest" / f"{name}.py"
            for name in ("api_client", "ui_state", "streamlit_ui")
        ),
    ]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in imports:
                assert not module.startswith("sqlalchemy")
                assert not module.startswith("nourish_nest") or module in allowed
