"""End-to-end: seed graph, run maintenance, search returns hybrid
results, episodes appear after simulated tool events."""
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_full_pipeline(tmp_path):
    mem = tmp_path / ".memory"
    mem.mkdir()
    graph = mem / "graph.jsonl"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    seed = [{
        "type": "entity", "name": "AuthService",
        "entityType": "service",
        "observations": ["Handles login and JWT verification"],
        "_created": now, "_updated": now,
    }]
    with open(graph, "w") as f:
        for e in seed:
            f.write(json.dumps(e) + "\n")

    r = subprocess.run(
        [sys.executable, str(REPO / "maintenance.py"),
         str(tmp_path), "--force"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (mem / "vec_index.npz").exists()
    assert (mem / "tfidf_index.json").exists()

    r = subprocess.run(
        [sys.executable, str(REPO / "memory-cli.py"),
         "search", "sign in handler"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "AuthService" in r.stdout, r.stdout

    payload = {"tool_name": "Bash",
               "tool_input": {"command": "broken-cmd"},
               "tool_response": {"error": "command not found"}}
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(payload))
    r = subprocess.run(
        [sys.executable,
         str(REPO / "hooks" / "capture_tool_context.py"),
         "--mint-error", str(input_file), str(graph)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr

    lines = graph.read_text().splitlines()
    assert any('"[ERROR]' in l for l in lines)

    subprocess.run(
        [sys.executable, str(REPO / "maintenance.py"),
         str(tmp_path), "--force"],
        check=True, capture_output=True,
    )

    r = subprocess.run(
        [sys.executable, str(REPO / "memory-cli.py"),
         "search", "broken-cmd"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "episode:err" in r.stdout, r.stdout
