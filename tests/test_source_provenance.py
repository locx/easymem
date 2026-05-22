"""_source stamping + secret scrub on commit episodes."""
import json
import subprocess
import sys
from pathlib import Path

HOOK = str(
    Path(__file__).resolve().parents[1]
    / "hooks" / "capture_tool_context.py"
)


def _seed(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    g = mem / "graph.jsonl"
    g.touch()
    return g


def _entity_lines(graph):
    return [
        json.loads(l) for l in graph.read_text().splitlines()
        if l.strip() and '"type":"entity"' in l
    ]


def test_commit_episode_carries_source(tmp_path):
    graph = _seed(tmp_path)
    subprocess.run(
        [sys.executable, HOOK, "--mint-commit",
         str(graph), "abc12345", "feat: regular commit"],
        check=True, capture_output=True,
    )
    ents = _entity_lines(graph)
    assert ents
    assert ents[0].get("_source", "").startswith(
        "hook:capture-tool-context:"
    )


def test_commit_episode_scrubs_token_in_msg(tmp_path):
    graph = _seed(tmp_path)
    leaked = "fix: pushed wrong token ghp_" + "A" * 30
    subprocess.run(
        [sys.executable, HOOK, "--mint-commit",
         str(graph), "deadbeef", leaked],
        check=True, capture_output=True,
    )
    text = graph.read_text()
    assert "[REDACTED]" in text
    assert "ghp_AAAA" not in text
