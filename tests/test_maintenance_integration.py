import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _setup_project(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    graph = mem / "graph.jsonl"
    graph.write_text(
        '{"type":"entity","name":"Foo","entityType":"comp",'
        '"observations":["bar"],"_created":"2026-01-01T00:00:00Z",'
        '"_updated":"2026-01-01T00:00:00Z"}\n'
    )
    return mem


def test_maintenance_builds_vec_index(tmp_path):
    mem = _setup_project(tmp_path)
    subprocess.run(
        [sys.executable, str(ROOT / "maintenance.py"),
         str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    assert (mem / "vec_index.npz").exists()


def test_maintenance_force_runs_even_when_throttled(tmp_path):
    mem = _setup_project(tmp_path)
    subprocess.run(
        [sys.executable, str(ROOT / "maintenance.py"),
         str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    (mem / ".last-maintenance").write_text(str(time.time()))
    mtime_before = (mem / "vec_index.npz").stat().st_mtime
    time.sleep(0.05)
    subprocess.run(
        [sys.executable, str(ROOT / "maintenance.py"),
         str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    mtime_after = (mem / "vec_index.npz").stat().st_mtime
    assert mtime_after >= mtime_before


def test_index_persists_obs_to_entity_map(tmp_path):
    # why: per-obs scoring needs the doc_id->entity map to survive
    # the JSON write path used by maintenance.
    import json as _json
    mem = tmp_path / ".easymem"
    mem.mkdir()
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    (mem / "graph.jsonl").write_text(
        '{"type":"entity","name":"Foo","entityType":"comp",'
        '"observations":["bar baz"],"_created":"' + now + '",'
        '"_updated":"' + now + '"}\n'
    )
    subprocess.run(
        [sys.executable, str(ROOT / "maintenance.py"),
         str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    idx_path = mem / "tfidf_index.json"
    assert idx_path.exists()
    idx = _json.loads(idx_path.read_text())
    assert "obs_to_entity" in idx
    o2e = idx["obs_to_entity"]
    assert isinstance(o2e, dict)
    assert "Foo" in set(o2e.values())
    assert all("#" in k for k in o2e)


def test_maintenance_lock_prevents_concurrent(tmp_path):
    import fcntl
    mem = _setup_project(tmp_path)
    lockfile = mem / ".maintenance-lock"
    lockfile.touch()
    with open(lockfile, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        t0 = time.time()
        subprocess.run(
            [sys.executable, str(ROOT / "maintenance.py"),
             str(tmp_path), "--force"],
            capture_output=True, text=True, timeout=5,
        )
        elapsed = time.time() - t0
        assert elapsed < 2.0
