import json

from semantic_server import graph, tools


def _count_records(path, name):
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "entity" and obj.get("name") == name:
                n += 1
    return n


def test_create_entities_skips_exact_name_duplicate(tmp_path):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    graph_path = tmp_path / "graph.jsonl"

    tools.create_entities(
        [{"name": "foo", "entityType": "note", "observations": ["a"]}],
        memory_dir,
    )
    result = tools.create_entities(
        [{"name": "foo", "entityType": "note", "observations": ["b"]}],
        memory_dir,
    )

    assert _count_records(graph_path, "foo") == 1
    assert result.get("created") == 0
    assert result.get("skipped") == 1
