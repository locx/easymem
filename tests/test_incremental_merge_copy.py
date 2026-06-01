import json

from semantic_server import graph


def test_incremental_merge_does_not_mutate_prior_snapshot(tmp_path):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    gp = tmp_path / "graph.jsonl"
    gp.write_text(
        json.dumps({
            "type": "entity", "name": "e",
            "entityType": "x", "observations": ["a"],
        }) + "\n",
        encoding="utf-8",
    )

    snapshot = graph.load_graph_entities(memory_dir)
    assert snapshot["e"]["observations"] == ["a"]

    with open(gp, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "entity", "name": "e",
            "entityType": "x", "observations": ["b"],
        }) + "\n")
    graph.invalidate_entity_cache_only()

    merged = graph.load_graph_entities(memory_dir)
    assert set(merged["e"]["observations"]) == {"a", "b"}
    # why: copy-on-merge — a reader holding the prior dict sees no partial state.
    assert snapshot["e"]["observations"] == ["a"]
    assert snapshot is not merged
