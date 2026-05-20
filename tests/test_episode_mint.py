import json
import subprocess
import sys
from pathlib import Path

HOOK = str(
    Path(__file__).resolve().parents[1]
    / "hooks" / "capture_tool_context.py"
)


def _seed(tmp_path):
    mem = tmp_path / ".memory"
    mem.mkdir()
    (mem / "graph.jsonl").touch()
    return mem / "graph.jsonl"


def test_error_episode_minted(tmp_path):
    graph = _seed(tmp_path)
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /no-such"},
        "tool_response": {
            "error": "rm: /no-such: No such file or directory"
        },
    }
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(payload))
    subprocess.run(
        [sys.executable, HOOK, "--mint-error",
         str(input_file), str(graph)],
        check=True, capture_output=True,
    )
    lines = graph.read_text().splitlines()
    assert any('"entityType":"episode"' in l for l in lines)
    assert any('[ERROR]' in l for l in lines)


def test_duplicate_error_uses_same_name(tmp_path):
    graph = _seed(tmp_path)
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /no-such"},
        "tool_response": {
            "error": "rm: /no-such: No such file or directory"
        },
    }
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(payload))
    for _ in range(2):
        subprocess.run(
            [sys.executable, HOOK, "--mint-error",
             str(input_file), str(graph)],
            check=True,
        )
    lines = [
        json.loads(l) for l in graph.read_text().splitlines()
        if l.strip()
    ]
    names = [l["name"] for l in lines if l.get("type") == "entity"]
    assert len(set(names)) == 1


def test_churn_episode_minted_at_3rd_edit(tmp_path, monkeypatch):
    graph = _seed(tmp_path)
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-sid-churn")
    target = str(tmp_path / "foo.py")
    payload = {"tool_name": "Edit", "tool_input": {"file_path": target}}
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(payload))
    for _ in range(3):
        subprocess.run(
            [sys.executable, HOOK, "--mint-churn",
             str(input_file), str(graph)],
            check=True, capture_output=True,
        )
    lines = graph.read_text().splitlines()
    assert sum('[CHURN]' in l for l in lines) == 1


def test_commit_episode_minted(tmp_path):
    graph = _seed(tmp_path)
    subprocess.run(
        [sys.executable, HOOK, "--mint-commit",
         str(graph), "abc12345", "feat: my commit"],
        check=True, capture_output=True,
    )
    lines = graph.read_text().splitlines()
    assert any('[COMMIT]' in l for l in lines)
    assert any('abc12345' in l for l in lines)
