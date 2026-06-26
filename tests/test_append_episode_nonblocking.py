import importlib.util
import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_fcntl = pytest.importorskip("fcntl")


def _load():
    spec = importlib.util.spec_from_file_location(
        "capture_tool_context", ROOT / "hooks" / "capture_tool_context.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hold_lock(lock_path, ready, release):
    fd = open(lock_path, "a")
    _fcntl.flock(fd.fileno(), _fcntl.LOCK_EX)
    ready.set()
    release.wait(30)
    _fcntl.flock(fd.fileno(), _fcntl.LOCK_UN)
    fd.close()


def test_append_defers_to_pending_under_contention(tmp_path):
    cap = _load()
    graph_path = str(tmp_path / "graph.jsonl")
    lock_path = str(tmp_path / ".graph.lock")

    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(
        target=_hold_lock, args=(lock_path, ready, release))
    holder.start()
    try:
        assert ready.wait(30), "child failed to take the lock"
        # why: deadline in the hook is 0.5s; 3x margin for slow CI proves the
        # call returns rather than blocking on the ~30s-held lock.
        start = time.monotonic()
        cap._append_episode(
            graph_path, "ep-contended", ["payload-under-contention"])
        elapsed = time.monotonic() - start
        assert elapsed < 1.5, f"call hung under contention ({elapsed}s)"
    finally:
        release.set()
        holder.join(30)

    assert not os.path.exists(graph_path), "should not write graph during lock"
    pending = graph_path + ".pending"
    assert os.path.exists(pending), "episode not deferred to .pending"
    assert "payload-under-contention" in Path(pending).read_text()


def test_append_writes_graph_when_lock_free(tmp_path):
    cap = _load()
    graph_path = str(tmp_path / "graph.jsonl")
    cap._append_episode(graph_path, "ep-free", ["payload-uncontended"])
    assert os.path.exists(graph_path)
    assert "payload-uncontended" in Path(graph_path).read_text()
    assert not os.path.exists(graph_path + ".pending")
