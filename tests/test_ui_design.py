from pathlib import Path

import pytest
from test_ui import HOME, Server, app

from nourish_nest import ui_design
from nourish_nest.ui_state import PAGES


def test_navigation_groups_preserve_existing_pages():
    assert set(ui_design.NAV_PAGES) == set(PAGES)
    assert len(ui_design.NAV_PAGES) == len(set(ui_design.NAV_PAGES))
    assert list(ui_design.NAV_GROUPS) == ["Home", "Plan", "Shop", "Manage", "Profile"]


@pytest.mark.parametrize("page", PAGES)
def test_each_page_has_accessible_local_artwork(page):
    _, key, _ = ui_design.HEADERS[page]
    markup = ui_design.image_html(key)
    assert "alt=" in markup
    assert "https://" not in markup and "http://" not in markup
    relative, alt = ui_design.IMAGES[key]
    assert (ui_design.ASSETS / relative).is_file()
    assert alt in markup


def test_missing_image_fallback_and_escaped_household_content(monkeypatch, tmp_path):
    monkeypatch.setattr(ui_design, "ASSETS", tmp_path)
    assert 'role="img"' in ui_design.image_html("kitchen")
    assert "unavailable" in ui_design.image_html("kitchen")
    assert "<script>" not in ui_design.safe("<script>test</script>")
    assert HOME["id"] not in ui_design.safe(HOME["id"])


def test_image_budget_and_responsive_accessibility_tokens():
    assert sum(p.stat().st_size for p in ui_design.ASSETS.rglob("*.webp")) < 350_000
    css = (ui_design.ASSETS / "theme.css").read_text()
    assert "max-width:1100px" in css and "max-width:768px" in css
    assert "prefers-reduced-motion:reduce" in css and ":focus-visible" in css
    assert "overflow-wrap: anywhere" in css
    assert (ui_design.ASSETS / "ATTRIBUTION.md").is_file()


def test_dashboard_and_pantry_render_do_not_trigger_expiry_writes(monkeypatch):
    server = Server([HOME])
    ui = app(monkeypatch, server)
    ui.radio(key="page").set_value("Pantry").run()
    ui.radio(key="page").set_value("Dashboard").run()
    assert not ui.exception
    assert ui.session_state["household_id"] == HOME["id"]
    assert all(r.method == "GET" for r in server.calls)
    assert all(r.url.path in ("/health", "/v1/households") for r in server.calls)
    assert all(not e.proto.expanded for e in ui.expander if e.label == "Technical details")


def test_runtime_assets_have_no_remote_image_dependencies():
    source = Path(ui_design.__file__).read_text(encoding="utf-8")
    assert "requests." not in source and "httpx." not in source
    assert "app/static/assets/" in source
