"""Rewrites must persist the full on-disk state, not a cached or
last-line view: obs deltas, >MAX_CACHED_OBS observations, other rows,
and appends racing the rewrite all have to survive.
"""
import threading

from semantic_server import tools
from semantic_server.code_index import index_project
from semantic_server.graph import append_jsonl, invalidate_caches
from semantic_server.io_utils import partition_graph


def _all_obs(graph_path, name):
    obs = []
    entities, _, _ = partition_graph(str(graph_path))
    for e in entities:
        if e.get("name") == name:
            obs.extend(e.get("observations", []))
    return obs


def _mem(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    invalidate_caches()
    return mem


def test_index_project_preserves_obs_deltas(tmp_path):
    mem = _mem(tmp_path)
    proj = tmp_path / "src"
    proj.mkdir()
    tools.create_entities(
        [{"name": "AuthService", "entityType": "component",
          "observations": ["uses JWT"]}],
        str(mem),
    )
    tools.add_observations(
        "AuthService", ["rotates refresh tokens"], str(mem)
    )
    index_project(str(mem), str(proj))
    obs = _all_obs(mem / "graph.jsonl", "AuthService")
    assert "uses JWT" in obs
    assert "rotates refresh tokens" in obs


def test_delete_preserves_full_observations_of_others(tmp_path):
    mem = _mem(tmp_path)
    tools.create_entities(
        [{"name": "Big", "entityType": "component",
          "observations": [f"obs {i}" for i in range(15)]},
         {"name": "Victim", "entityType": "component",
          "observations": ["x"]}],
        str(mem),
    )
    tools.add_observations(
        "Big", [f"obs {i}" for i in range(15, 25)], str(mem)
    )
    res = tools.delete_entities(["Victim"], str(mem))
    assert res.get("deleted") == 1
    obs = _all_obs(mem / "graph.jsonl", "Big")
    assert len(set(obs)) == 25


def test_delete_preserves_other_rows(tmp_path):
    mem = _mem(tmp_path)
    tools.create_entities(
        [{"name": "Keep", "entityType": "component",
          "observations": ["a"]},
         {"name": "Victim", "entityType": "component",
          "observations": ["x"]}],
        str(mem),
    )
    with open(mem / "graph.jsonl", "a", encoding="utf-8") as f:
        f.write('{"type":"checkpoint","v":1}\n')
    invalidate_caches()
    res = tools.delete_entities(["Victim"], str(mem))
    assert res.get("deleted") == 1
    _, _, others = partition_graph(str(mem / "graph.jsonl"))
    assert any(o.get("type") == "checkpoint" for o in others)


def test_remove_observations_preserves_others_full_obs(tmp_path):
    mem = _mem(tmp_path)
    tools.create_entities(
        [{"name": "Big", "entityType": "component",
          "observations": [f"obs {i}" for i in range(15)]},
         {"name": "Target", "entityType": "component",
          "observations": ["keep", "drop"]}],
        str(mem),
    )
    tools.add_observations(
        "Big", [f"obs {i}" for i in range(15, 25)], str(mem)
    )
    res = tools.remove_observations("Target", ["drop"], str(mem))
    assert res.get("removed") == 1
    obs = _all_obs(mem / "graph.jsonl", "Big")
    assert len(set(obs)) == 25


def test_locked_rewrite_does_not_lose_concurrent_append(tmp_path):
    from semantic_server.graph import rewrite_graph_locked

    mem = _mem(tmp_path)
    tools.create_entities(
        [{"name": "Existing", "entityType": "component",
          "observations": ["a"]}],
        str(mem),
    )
    in_transform = threading.Event()
    appended = threading.Event()

    def _append_racer():
        in_transform.wait(timeout=5)
        append_jsonl(str(mem), [{
            "type": "entity", "name": "Raced",
            "entityType": "component", "observations": ["r"],
        }])
        appended.set()

    racer = threading.Thread(target=_append_racer)
    racer.start()

    def _transform(entities, relations):
        in_transform.set()
        # why: give the racer time to attempt its append; it must block
        # on the lock until this rewrite commits.
        appended.wait(timeout=0.5)
        return dict(entities), list(relations)

    rewrite_graph_locked(str(mem), _transform)
    racer.join(timeout=10)
    entities, _, _ = partition_graph(str(mem / "graph.jsonl"))
    names = {e.get("name") for e in entities}
    assert "Existing" in names
    assert "Raced" in names
