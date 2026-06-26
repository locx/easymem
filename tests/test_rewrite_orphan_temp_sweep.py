import os

from semantic_server import graph


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _find_dead_pid():
    # why: a fixed sentinel (e.g. 999999) may be a live pid on Linux; probe
    # downward from a high value for one that provably has no process.
    for pid in range(4194303, 1, -1):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except OSError:
            continue
    raise RuntimeError("no dead pid found")


def test_locked_rewrite_sweeps_orphan_new_temps(tmp_path):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    graph_path = os.path.join(memory_dir, "graph.jsonl")
    _write(graph_path, '{"type": "entity", "name": "a", "observations": []}\n')

    # Temp owned by a dead pid — must be swept.
    dead = f"{graph_path}.new.{_find_dead_pid()}.123456789"
    _write(dead, "garbage\n")

    # Temp owned by the current (live) pid — sweeping it would delete a live
    # writer's in-flight temp, so it must survive.
    live = f"{graph_path}.new.{os.getpid()}.123456789"
    _write(live, "live\n")

    # Sidecars that share the graph basename but are not .new.* — untouched.
    pending = graph_path + ".pending"
    pending_proc = graph_path + ".pending.processing"
    _write(pending, "y\n")
    _write(pending_proc, "x\n")

    def transform(entities, relations):
        return entities, relations

    assert graph.rewrite_graph_locked(memory_dir, transform) is True

    assert not os.path.exists(dead)
    assert os.path.exists(live)
    assert os.path.exists(graph_path)
    assert os.path.exists(pending)
    assert os.path.exists(pending_proc)
    os.unlink(live)


def test_normal_rewrite_round_trips(tmp_path):
    graph.invalidate_caches()
    memory_dir = str(tmp_path)
    ent = {"entityType": "note", "observations": ["hi"]}
    graph.rewrite_graph(memory_dir, {"a": ent}, [])

    entities = graph.load_graph_entities(memory_dir)
    assert "a" in entities
    assert entities["a"]["observations"] == ["hi"]
    # No temp files left behind by a clean rewrite.
    leftovers = [n for n in os.listdir(memory_dir) if ".new." in n]
    assert leftovers == []
