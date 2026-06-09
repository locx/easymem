import json
import os

import pytest

import maintenance
from semantic_server import graph, recall, slots


def _load_capture():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "_cap3", os.path.join(root, "hooks", "capture_tool_context.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_budget_aborted_parse_marks_truncated_and_forces_reread(
        tmp_path, monkeypatch):
    monkeypatch.setattr(graph, "PARSE_TIME_BUDGET", -1.0)
    graph.invalidate_caches()
    gp = tmp_path / "graph.jsonl"
    lines = [json.dumps({"type": "entity", "name": f"e{i}",
                         "entityType": "x", "observations": ["o"]})
             for i in range(1500)]
    gp.write_text("\n".join(lines) + "\n")

    ents = graph.load_graph_entities(str(tmp_path))
    assert graph.entity_cache["truncated"] is True
    assert len(ents) < 1500
    # No matching mtime is stamped, so the next load re-parses.
    assert graph.entity_cache["mtime"] is None
    with pytest.raises(OSError):
        graph.rewrite_graph(str(tmp_path), ents, [])


def test_rebuild_index_clears_index_when_no_entities(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text(json.dumps({
        "type": "relation", "from": "a", "to": "b", "relationType": "r",
    }) + "\n")
    stale = mem / "tfidf_index.json"
    stale.write_text("{}")

    maintenance.rebuild_index(str(mem))
    assert not stale.exists()


def test_set_slot_rejects_non_string(tmp_path):
    key = next(iter(slots.SLOT_KEYS))
    with pytest.raises(TypeError):
        slots.set_slot(str(tmp_path), key, 123)


def test_failed_flush_rearms_dirty_and_keeps_deltas(tmp_path, monkeypatch):
    recall.init_recall_state(str(tmp_path))
    recall.record_recalls(["X"])
    assert recall.recall_dirty is True

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    recall.flush_recall_counts()

    assert recall.recall_dirty is True
    assert recall._unflushed.get("X") == 1


def test_mint_error_tolerates_non_dict_payload(tmp_path):
    cap = _load_capture()
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({
        "tool_name": "Bash",
        "tool_input": "not-a-dict",
        "tool_response": "also-not-a-dict",
    }))
    gp = str(tmp_path / "graph.jsonl")
    cap.mint_error(str(inp), gp)  # must not raise
