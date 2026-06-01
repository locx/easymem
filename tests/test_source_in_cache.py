import json

import semantic_server.search as search
from semantic_server import graph


def test_source_retained_in_cache_and_mapped(tmp_path):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    (tmp_path / "graph.jsonl").write_text(
        json.dumps({
            "type": "entity", "name": "e", "entityType": "episode",
            "_source": "episode:abc", "observations": ["o"],
        }) + "\n",
        encoding="utf-8",
    )

    ents = graph.load_graph_entities(memory_dir)
    # why: _source must survive in the cache so rewrites don't strip it and
    # session diversification can read it without a full-file scan.
    assert ents["e"].get("_source") == "episode:abc"

    src_map = search._load_source_map(memory_dir, ["e", "missing"])
    assert src_map == {"e": "episode:abc"}
