"""Focused checks for demo fixtures and delivered recording assets only."""
import json
import re

import pytest
from PIL import Image
from setup import OUT, populate
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from story import SCENES

from nourish_nest.database import Base
from nourish_nest.models import Food, HouseholdMember, PantryItem, Recipe


def test_seed_is_repeatable_and_fictional():
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        populate(engine)
        populate(engine)
        with Session(engine) as session:
            for model, count in [(Food, 10), (HouseholdMember, 2), (PantryItem, 11), (Recipe, 7)]:
                assert session.scalar(select(func.count()).select_from(model)) == count
            assert {m.name for m in session.scalars(select(HouseholdMember))} == {"Maya", "Daniel"}
    finally:
        engine.dispose()


def test_refuses_normal_database(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "normal.db").as_posix())
    try:
        with pytest.raises(ValueError, match="Refusing"):
            populate(engine)
    finally:
        engine.dispose()


def test_narration_duration_and_no_private_identifiers():
    assert sum(row[0] for row in SCENES) == 360
    text = " ".join(row[-1] for row in SCENES)
    assert 750 <= len(text.split()) <= 900
    assert not re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-", text)
    assert "C:\\Users" not in text


def test_delivered_frame_sequence():
    timeline = json.loads((OUT / "timeline.json").read_text())
    assert abs(sum(c["duration"] for c in timeline) - 360) < .001
    end = 0
    for cue in timeline:
        assert abs(cue["start"] - end) < .001
        assert cue["caption"]
        with Image.open(OUT / cue["file"]) as image:
            assert image.size == (1920, 1080)
        end = cue["start"] + cue["duration"]


def test_evidence_distinguishes_ui_and_api_replay():
    proof = json.loads((OUT / "completed-proof.json").read_text())
    assert proof["same_grocery_list"] and proof["replayed"]
    assert not proof["replay_changed_domain_data"]
    assert "Separate API fixture" in proof["replay_method"]
    assert proof["item_count"] == 8


def test_local_player_requires_no_network():
    player = (OUT / "presentation.html").read_text(encoding="utf-8")
    assert not re.search(r"https?://", player)
    assert "fetch(" not in player
    assert (OUT / "nourishnest-demo-storyboard.pdf").read_bytes().startswith(b"%PDF")
