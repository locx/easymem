from datetime import datetime, timedelta, timezone

from semantic_server.workflows import extract_workflows


def _ep(name, files, neighbors, created):
    return name, {
        "entityType": "episode",
        "observations": files,
        "_created": created,
        "_neighbors": neighbors,
    }


def test_mints_workflow_for_three_overlapping_episodes():
    entities = dict([
        _ep("episode:churn:1",
            ["src/auth.py", "src/session.py"],
            ["AuthService", "SessionStore"],
            "2026-05-01T00:00:00Z"),
        _ep("episode:churn:2",
            ["src/auth.py", "src/session.py", "src/api.py"],
            ["AuthService", "SessionStore"],
            "2026-05-10T00:00:00Z"),
        _ep("episode:churn:3",
            ["src/auth.py", "src/session.py"],
            ["AuthService", "SessionStore"],
            "2026-05-15T00:00:00Z"),
    ])
    workflows, relations = extract_workflows(
        entities, now_iso="2026-05-20T00:00:00Z", window_days=30,
        min_episodes=3, min_shared_obs=2, min_shared_neighbors=2,
    )
    assert len(workflows) == 1
    wf = workflows[0]
    assert wf["entityType"] == "workflow"
    assert "src/auth.py" in " ".join(wf["observations"])
    assert sum(1 for r in relations
               if r["relationType"] == "derived-from"
               and r["from"] == wf["name"]) == 3


def test_no_workflow_below_threshold():
    entities = dict([
        _ep("episode:churn:1",
            ["src/auth.py", "src/session.py"],
            ["AuthService"], "2026-05-01T00:00:00Z"),
        _ep("episode:churn:2",
            ["src/auth.py"], ["AuthService"], "2026-05-10T00:00:00Z"),
    ])
    workflows, _ = extract_workflows(
        entities, now_iso="2026-05-20T00:00:00Z", window_days=30,
        min_episodes=3, min_shared_obs=2, min_shared_neighbors=2,
    )
    assert workflows == []


def test_no_workflow_below_neighbor_threshold():
    entities = dict([
        _ep("episode:churn:1", ["src/a.py", "src/b.py"],
            ["OnlyOne"], "2026-05-15T00:00:00Z"),
        _ep("episode:churn:2", ["src/a.py", "src/b.py"],
            ["OnlyOne"], "2026-05-16T00:00:00Z"),
        _ep("episode:churn:3", ["src/a.py", "src/b.py"],
            ["OnlyOne"], "2026-05-17T00:00:00Z"),
    ])
    workflows, _ = extract_workflows(
        entities, now_iso="2026-05-20T00:00:00Z", window_days=30,
        min_episodes=3, min_shared_obs=2, min_shared_neighbors=2,
    )
    assert workflows == []


def test_old_episodes_excluded_from_window():
    entities = dict([
        _ep("episode:churn:1", ["src/x.py"], ["X"],
            "2026-01-01T00:00:00Z"),
        _ep("episode:churn:2", ["src/x.py"], ["X"],
            "2026-01-02T00:00:00Z"),
        _ep("episode:churn:3", ["src/x.py"], ["X"],
            "2026-01-03T00:00:00Z"),
    ])
    workflows, _ = extract_workflows(
        entities, now_iso="2026-05-20T00:00:00Z", window_days=30,
        min_episodes=3, min_shared_obs=1, min_shared_neighbors=1,
    )
    assert workflows == []


import json
import subprocess
import sys
from pathlib import Path


def test_maintenance_mints_workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mem = tmp_path / ".easymem"
    mem.mkdir()
    lines = []
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for i in range(3):
        lines.append(json.dumps({
            "type": "entity",
            "name": f"episode:churn:{i}",
            "entityType": "episode",
            "observations": ["src/auth.py", "src/session.py"],
            "_neighbors": ["AuthService", "SessionStore"],
            "_created": recent,
        }))
    (mem / "graph.jsonl").write_text("\n".join(lines) + "\n")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "maintenance.py"),
                    "--force"], check=True, cwd=tmp_path)
    text = (mem / "graph.jsonl").read_text()
    assert '"entityType":"workflow"' in text or '"entityType": "workflow"' in text
