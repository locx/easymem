"""A secret-shaped entity name is stored scrubbed; delete_entities and
remove_observations must scrub the raw lookup name so it still resolves."""
import os

from semantic_server.graph import load_graph_entities
from semantic_server.text import scrub_identity
from semantic_server.tools import (
    create_entities,
    delete_entities,
    remove_observations,
)

_SENTINEL = "ghp_0123456789abcdefghijABCDEFGHIJ0123"


def _md(tmp_path):
    md = str(tmp_path / ".easymem")
    os.makedirs(md, exist_ok=True)
    return md


def test_delete_resolves_raw_secret_name(tmp_path):
    md = _md(tmp_path)
    create_entities([{"name": _SENTINEL, "entityType": "file"}], md)
    res = delete_entities([_SENTINEL], md)
    assert res.get("deleted") == 1
    assert scrub_identity(_SENTINEL) not in load_graph_entities(md)


def test_remove_observations_resolves_raw_secret_name(tmp_path):
    md = _md(tmp_path)
    create_entities([
        {"name": _SENTINEL, "entityType": "file",
         "observations": ["keep", "drop"]},
    ], md)
    res = remove_observations(_SENTINEL, ["drop"], md)
    assert res.get("removed") == 1
    obs = load_graph_entities(md)[scrub_identity(_SENTINEL)]["observations"]
    assert "drop" not in obs
    assert "keep" in obs
