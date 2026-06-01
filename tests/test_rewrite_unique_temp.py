import os

from semantic_server import graph


def test_rewrite_graph_uses_unique_temp_per_call(tmp_path, monkeypatch):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    final = os.path.join(memory_dir, "graph.jsonl")

    seen = []
    real_replace = os.replace

    def capture(src, dst):
        seen.append(src)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", capture)

    ent = {"entityType": "note", "observations": []}
    graph.rewrite_graph(memory_dir, {"a": ent}, [])
    graph.rewrite_graph(memory_dir, {"b": ent}, [])

    assert len(seen) == 2
    # why: a shared temp path lets concurrent rewriters clobber each other.
    assert seen[0] != seen[1]
    assert all(src != final for src in seen)
