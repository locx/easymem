import pytest

from semantic_server import cache, graph


def teardown_function(_func):
    cache.clear_entity_cache()
    cache.clear_relation_cache()


def test_rewrite_refuses_when_cache_truncated(tmp_path):
    # why: the parser caps at MAX_ENTITY_COUNT; persisting that snapshot would
    # permanently drop entities beyond the cap.
    cache.entity_cache["truncated"] = True
    with pytest.raises(OSError, match="truncated entity cache"):
        graph.rewrite_graph(str(tmp_path), {"a": {}}, [])


def test_rewrite_proceeds_when_not_truncated(tmp_path):
    cache.entity_cache["truncated"] = False
    graph_path = tmp_path / "graph.jsonl"
    graph_path.write_text("", encoding="utf-8")
    graph.rewrite_graph(
        str(tmp_path),
        {"AuthService": {
            "entityType": "service",
            "observations": ["x"],
        }},
        [],
    )
    assert graph_path.exists()
    assert "AuthService" in graph_path.read_text(encoding="utf-8")
