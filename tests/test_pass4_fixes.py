import json
import os

from semantic_server import graph, recall, tools


def _load_capture():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "_cap4", os.path.join(root, "hooks", "capture_tool_context.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_list_decisions_reports_latest_outcome(tmp_path):
    graph.invalidate_caches()
    md = str(tmp_path)
    line = json.dumps({
        "type": "entity", "name": "decision: UseX", "entityType": "decision",
        "observations": ["Rationale: x", "Outcome: pending",
                         "Outcome: successful"],
        "_updated": "2026-06-01T00:00:00Z",
    })
    (tmp_path / "graph.jsonl").write_text(line + "\n")

    resp = tools.list_decisions(md)
    d = next(x for x in resp["decisions"] if x["title"] == "UseX")
    assert d["outcome"] == "successful"
    # And it must NOT surface as a stale pending decision.
    stale = tools.list_decisions(md, stale_days=0)
    assert all(x["title"] != "UseX" for x in stale["decisions"])


def test_unflushed_bounded_by_eviction(tmp_path, monkeypatch):
    monkeypatch.setattr(recall, "MAX_RECALL_ENTRIES", 3)
    recall.init_recall_state(str(tmp_path))
    for i in range(12):
        recall.record_recalls([f"n{i}"])
    assert len(recall.recall_counts) <= 3
    assert len(recall._unflushed) <= 3


def test_evicted_entry_not_resurrected_on_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(recall, "MAX_RECALL_ENTRIES", 2)
    path = tmp_path / "recall_counts.json"
    recall.init_recall_state(str(tmp_path))
    recall.record_recalls(["a"])
    recall.record_recalls(["b"])
    recall.record_recalls(["c"])
    assert "a" not in recall.recall_counts

    path.write_text(json.dumps({"b": 1, "c": 1}))
    recall._last_recall_check = 0.0
    recall.recall_mtime = -1.0
    recall.maybe_reload_recall_counts()
    assert "a" not in recall.recall_counts


def test_mint_churn_tolerates_non_dict_input(tmp_path):
    cap = _load_capture()
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"tool_name": "Edit", "tool_input": "nope"}))
    cap.mint_churn(str(inp), str(tmp_path / "graph.jsonl"))  # must not raise
