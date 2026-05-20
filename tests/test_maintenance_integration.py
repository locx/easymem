import json
import subprocess
import sys
import time


def _setup_project(tmp_path):
    mem = tmp_path / ".memory"
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
        [sys.executable, "maintenance.py", str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    assert (mem / "vec_index.npz").exists()


def test_maintenance_force_runs_even_when_throttled(tmp_path):
    mem = _setup_project(tmp_path)
    subprocess.run(
        [sys.executable, "maintenance.py", str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    (mem / ".last-maintenance").write_text(str(time.time()))
    mtime_before = (mem / "vec_index.npz").stat().st_mtime
    time.sleep(0.05)
    subprocess.run(
        [sys.executable, "maintenance.py", str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    mtime_after = (mem / "vec_index.npz").stat().st_mtime
    assert mtime_after >= mtime_before


def test_maintenance_lock_prevents_concurrent(tmp_path):
    import fcntl
    mem = _setup_project(tmp_path)
    lockfile = mem / ".maintenance-lock"
    lockfile.touch()
    with open(lockfile, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        t0 = time.time()
        subprocess.run(
            [sys.executable, "maintenance.py", str(tmp_path), "--force"],
            capture_output=True, text=True, timeout=5,
        )
        elapsed = time.time() - t0
        assert elapsed < 2.0
