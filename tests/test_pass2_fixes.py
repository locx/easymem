import json
import os
import time

import numpy as np

from semantic_server import recall, traverse, vector
from semantic_server import code_index
from semantic_server import graph


def test_recall_reload_preserves_unflushed_increments(tmp_path):
    path = tmp_path / "recall_counts.json"
    path.write_text(json.dumps({"A": 5}))
    recall.init_recall_state(str(tmp_path))
    recall.record_recalls(["A", "B"])

    # An external writer rewrites the file without our pending deltas.
    time.sleep(0.01)
    path.write_text(json.dumps({"A": 5}))
    recall._last_recall_check = 0.0
    recall.recall_mtime = -1.0
    recall.maybe_reload_recall_counts()

    assert recall.recall_counts.get("A") == 6
    assert recall.recall_counts.get("B") == 1


def test_vector_index_roundtrips_long_names(tmp_path):
    name = "file:" + "a/" * 200 + "x.py"
    assert len(name) > 256
    vecs = np.ones((1, vector.EMBED_DIM), dtype=np.int8)
    vector.save_index(str(tmp_path / "vec_index.npz"), [name], vecs, "m")

    idx = vector.load_index(str(tmp_path))
    assert idx["names"][0] == name


def test_traverse_emits_no_edges_to_capped_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(traverse, "_MAX_VISITED", 3)
    graph.invalidate_caches()
    gp = tmp_path / "graph.jsonl"
    lines = [json.dumps({"type": "entity", "name": "hub",
                         "entityType": "x", "observations": []})]
    for i in range(8):
        lines.append(json.dumps({"type": "entity", "name": f"leaf{i}",
                                 "entityType": "x", "observations": []}))
        lines.append(json.dumps({"type": "relation", "from": "hub",
                                 "to": f"leaf{i}", "relationType": "r"}))
    gp.write_text("\n".join(lines) + "\n")

    res = traverse.traverse_relations("hub", str(tmp_path), max_depth=2)
    node_names = {n["name"] for n in res["nodes"]}
    for e in res["edges"]:
        assert e["from"] in node_names
        assert e["to"] in node_names


def test_code_scan_detects_deletion(tmp_path):
    proj = tmp_path
    mem = tmp_path / ".easymem"
    mem.mkdir()
    (proj / "a.py").write_text("x = 1\n")
    (proj / "b.py").write_text("y = 2\n")

    code_index.touch_code_stamp(str(mem), str(proj))
    assert code_index.code_scan_is_stale(str(mem), str(proj)) is False

    os.unlink(proj / "b.py")
    # Deletion leaves no file newer than the stamp, but the path-set changed.
    assert code_index.code_scan_is_stale(str(mem), str(proj)) is True


def test_mint_error_scrubs_before_truncating(tmp_path):
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "_cap", os.path.join(root, "hooks", "capture_tool_context.py"))
    cap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cap)

    secret = "ghp_" + "a" * 36
    pad = "x" * 190
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": pad + secret},
        "tool_response": {"error": "boom"},
    }
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(payload))
    gp = str(tmp_path / "graph.jsonl")
    cap.mint_error(str(inp), gp)

    text = open(gp).read() if os.path.exists(gp) else ""
    assert secret not in text
