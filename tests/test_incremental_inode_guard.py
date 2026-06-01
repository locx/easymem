import json
import os

from semantic_server import graph


def _write_entities(path, names, obs="a"):
    lines = [
        json.dumps({
            "type": "entity", "name": n,
            "entityType": "x", "observations": [obs],
        })
        for n in names
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_incremental_load_detects_cross_process_rewrite(tmp_path):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    gp = tmp_path / "graph.jsonl"

    _write_entities(gp, [f"e{i}" for i in range(5)])
    first = graph.load_graph_entities(memory_dir)
    assert set(first) == {f"e{i}" for i in range(5)}

    # why: append_jsonl marks the cache append-only before the next read.
    graph.invalidate_entity_cache_only()

    # Another process rewrites to a new inode, larger size, different entities.
    repl = tmp_path / "graph.jsonl.repl"
    _write_entities(repl, [f"z{i}" for i in range(6)], obs="b" * 40)
    os.replace(repl, gp)

    second = graph.load_graph_entities(memory_dir)
    assert set(second) == {f"z{i}" for i in range(6)}
