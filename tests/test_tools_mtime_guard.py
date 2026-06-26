"""Rewrite ops hold the graph lock across load+rewrite, so they no
longer depend on mtime guards — they must succeed and stay correct
even while the file's mtime churns underneath them.
"""
import semantic_server.tools as tools
from semantic_server import graph


def _force_changing_mtime(monkeypatch):
    seq = iter(range(1, 100000))
    monkeypatch.setattr(tools.os.path, "getmtime", lambda p: next(seq))


def test_remove_observations_correct_despite_mtime_churn(
        tmp_path, monkeypatch):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    tools.create_entities(
        [{"name": "e", "entityType": "x", "observations": ["a", "b"]}],
        memory_dir,
    )
    _force_changing_mtime(monkeypatch)
    result = tools.remove_observations("e", ["a"], memory_dir)
    assert result.get("removed") == 1
    graph.invalidate_caches()
    ents = graph.load_graph_entities(memory_dir)
    assert ents["e"]["observations"] == ["b"]


def test_rename_entity_correct_despite_mtime_churn(tmp_path, monkeypatch):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    tools.create_entities(
        [{"name": "old", "entityType": "x", "observations": ["a"]}],
        memory_dir,
    )
    _force_changing_mtime(monkeypatch)
    result = tools.rename_entity("old", "new", memory_dir)
    assert result.get("renamed") == "old"
    graph.invalidate_caches()
    ents = graph.load_graph_entities(memory_dir)
    assert "new" in ents and "old" not in ents
