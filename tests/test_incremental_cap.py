import json

from semantic_server import graph


def _ent(name):
    return json.dumps({
        "type": "entity", "name": name,
        "entityType": "x", "observations": ["o"],
    })


def test_incremental_merge_enforces_entity_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(graph, "MAX_ENTITY_COUNT", 3)
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    gp = tmp_path / "graph.jsonl"
    gp.write_text(_ent("e0") + "\n" + _ent("e1") + "\n", encoding="utf-8")

    assert len(graph.load_graph_entities(memory_dir)) == 2

    with open(gp, "a", encoding="utf-8") as f:
        for i in range(2, 7):
            f.write(_ent(f"e{i}") + "\n")
    graph.invalidate_entity_cache_only()

    merged = graph.load_graph_entities(memory_dir)
    assert len(merged) <= 3
    assert graph.entity_cache["truncated"] is True


def test_incremental_load_resets_stale_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(graph, "MAX_ENTITY_COUNT", 2)
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    gp = tmp_path / "graph.jsonl"
    gp.write_text(
        _ent("e0") + "\n" + _ent("e1") + "\n" + _ent("e2") + "\n",
        encoding="utf-8",
    )

    graph.load_graph_entities(memory_dir)
    assert graph.entity_cache["truncated"] is True

    # File now fits under a raised cap; an incremental read must clear the
    # stale flag so rewrite_graph is no longer wedged.
    monkeypatch.setattr(graph, "MAX_ENTITY_COUNT", 100)
    with open(gp, "a", encoding="utf-8") as f:
        f.write(_ent("e3") + "\n")
    graph.invalidate_entity_cache_only()

    graph.load_graph_entities(memory_dir)
    assert graph.entity_cache["truncated"] is False


def test_incremental_relation_append_zeroes_adjacency_size(tmp_path):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    gp = tmp_path / "graph.jsonl"
    gp.write_text(_ent("e0") + "\n" + _ent("e1") + "\n", encoding="utf-8")
    graph.load_graph_entities(memory_dir)

    graph.adjacency_cache.update(
        outbound={"e0": ["e1"]}, inbound={"e1": ["e0"]}, size=4096, mtime=1.0
    )
    with open(gp, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "relation", "from": "e0", "to": "e1",
            "relationType": "rel",
        }) + "\n")
    graph.invalidate_entity_cache_only()
    graph.load_graph_entities(memory_dir)

    assert graph.adjacency_cache["outbound"] is None
    assert graph.adjacency_cache["size"] == 0
