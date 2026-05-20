import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = str(ROOT / "memory-cli.py")
MAINTENANCE = str(ROOT / "maintenance.py")


def _seed_project(tmp_path):
    mem = tmp_path / ".memory"
    mem.mkdir()
    graph = mem / "graph.jsonl"
    # Recent timestamps so prune_entities (MAX_AGE_DAYS=90) keeps them.
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    entries = [
        {"type": "entity", "name": "AuthService",
         "entityType": "service",
         "observations": ["Handles login JWT tokens"],
         "_created": now, "_updated": now},
        {"type": "entity", "name": "SyncManager",
         "entityType": "component",
         "observations": ["Uses LWW conflict resolution"],
         "_created": now, "_updated": now},
    ]
    with open(graph, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    subprocess.run(
        [sys.executable, MAINTENANCE, str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    return mem


def test_search_returns_results(tmp_path):
    _seed_project(tmp_path)
    out = subprocess.run(
        [sys.executable, CLI, "search", "authentication"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "AuthService" in out.stdout


def test_search_handles_paraphrase(tmp_path):
    """Vector layer should find AuthService for 'sign in flow'."""
    _seed_project(tmp_path)
    out = subprocess.run(
        [sys.executable, CLI, "search", "sign in flow"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "AuthService" in out.stdout


def test_search_falls_back_to_tfidf_when_vec_missing(tmp_path):
    _seed_project(tmp_path)
    (tmp_path / ".memory" / "vec_index.npz").unlink()
    out = subprocess.run(
        [sys.executable, CLI, "search", "JWT"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "AuthService" in out.stdout
