"""graph_stats surfaces a derived active/confirmed/stale breakdown."""
import os

from semantic_server.graph import append_jsonl
from semantic_server.tools import _stats_status_breakdown, graph_stats


def test_breakdown_present_in_stats(tmp_path):
    md = str(tmp_path / ".easymem")
    os.makedirs(md, exist_ok=True)
    append_jsonl(md, {"type": "entity", "name": "fresh",
                      "entityType": "file", "observations": ["x"],
                      "_created": "2099-01-01T00:00:00Z",
                      "_updated": "2099-01-01T00:00:00Z"})
    stats = graph_stats(md)
    assert "status_breakdown" in stats
    assert sum(stats["status_breakdown"].values()) == stats["entities"]


def test_old_unrecalled_is_stale():
    entities = {
        "old": {"entityType": "file", "observations": [],
                "_created": "2000-01-01T00:00:00Z",
                "_updated": "2000-01-01T00:00:00Z"},
        "new": {"entityType": "file", "observations": [],
                "_created": "2099-01-01T00:00:00Z",
                "_updated": "2099-01-01T00:00:00Z"},
    }
    out = _stats_status_breakdown(entities)
    assert out.get("stale", 0) == 1
    assert out.get("active", 0) == 1
