import semantic_server.tools as tools
from semantic_server import graph


def _force_changing_mtime(monkeypatch):
    seq = iter(range(1, 100000))
    monkeypatch.setattr(tools.os.path, "getmtime", lambda p: next(seq))


def test_remove_observations_detects_concurrent_write(tmp_path, monkeypatch):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    tools.create_entities(
        [{"name": "e", "entityType": "x", "observations": ["a", "b"]}],
        memory_dir,
    )
    _force_changing_mtime(monkeypatch)
    result = tools.remove_observations("e", ["a"], memory_dir)
    assert result.get("error") == "concurrent write"


def test_rename_entity_detects_concurrent_write(tmp_path, monkeypatch):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    tools.create_entities(
        [{"name": "old", "entityType": "x", "observations": ["a"]}],
        memory_dir,
    )
    _force_changing_mtime(monkeypatch)
    result = tools.rename_entity("old", "new", memory_dir)
    assert result.get("error") == "concurrent write"
