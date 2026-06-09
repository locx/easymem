import json
import os

from semantic_server import graph


def _load_migrate():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "_mig5", os.path.join(root, "scripts", "_migrate_auto_memory.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_budget_aborted_parse_does_not_cache_relations_as_complete(
        tmp_path, monkeypatch):
    monkeypatch.setattr(graph, "PARSE_TIME_BUDGET", -1.0)
    graph.invalidate_caches()
    gp = tmp_path / "graph.jsonl"
    lines = [json.dumps({"type": "relation", "from": f"a{i}",
                         "to": f"b{i}", "relationType": "r"})
             for i in range(1500)]
    gp.write_text("\n".join(lines) + "\n")

    rels = graph.load_graph_relations(str(tmp_path))
    assert len(rels) < 1500
    # No matching mtime stamped, so a later relation load re-parses.
    assert graph.relation_cache["mtime"] is None


def test_existing_names_skips_non_dict_lines(tmp_path):
    mig = _load_migrate()
    gp = tmp_path / "graph.jsonl"
    gp.write_text(
        json.dumps({"type": "entity", "name": "E"}) + "\n"
        "123\n\"text\"\nnull\n[1,2]\n"
    )
    assert mig._existing_names(str(gp)) == {"E"}
