import json
from pathlib import Path

from semantic_server.slots import (
    SLOT_KEYS, get_slot, set_slot, list_slots,
)


def test_slot_keys_are_fixed():
    assert SLOT_KEYS == ("persona", "preferences", "guidelines")


def test_set_and_get_roundtrip(tmp_path):
    set_slot(str(tmp_path), "persona", "senior Python engineer")
    assert get_slot(str(tmp_path), "persona") == "senior Python engineer"


def test_get_missing_returns_empty(tmp_path):
    assert get_slot(str(tmp_path), "preferences") == ""


def test_list_slots_returns_all_keys(tmp_path):
    set_slot(str(tmp_path), "persona", "p")
    set_slot(str(tmp_path), "guidelines", "g")
    out = list_slots(str(tmp_path))
    assert out == {"persona": "p", "preferences": "", "guidelines": "g"}


def test_set_unknown_key_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        set_slot(str(tmp_path), "nicknames", "x")


def test_set_writes_atomically(tmp_path):
    set_slot(str(tmp_path), "persona", "v1")
    slots_path = Path(tmp_path) / "slots.json"
    raw = json.loads(slots_path.read_text())
    assert raw["persona"] == "v1"
    set_slot(str(tmp_path), "persona", "v2")
    raw2 = json.loads(slots_path.read_text())
    assert raw2["persona"] == "v2"
    assert not (Path(tmp_path) / "slots.json.new").exists()
