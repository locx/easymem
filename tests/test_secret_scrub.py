import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "capture_tool_context", ROOT / "hooks" / "capture_tool_context.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_password_flag_equals_redacted():
    cap = _load()
    out = cap._scrub("mysqldump --password=hunter2 db")
    assert "hunter2" not in out
    assert "[REDACTED]" in out


def test_password_flag_space_redacted():
    cap = _load()
    out = cap._scrub("tool --password hunter2 --verbose")
    assert "hunter2" not in out
    assert "--verbose" in out


def test_token_and_api_key_flags_redacted():
    cap = _load()
    out = cap._scrub("cli --token tok123 --api-key=key456")
    assert "tok123" not in out
    assert "key456" not in out


def test_env_assignment_redacted():
    cap = _load()
    out = cap._scrub("export FOO_TOKEN=abc123 && run")
    assert "abc123" not in out
    assert "[REDACTED]" in out


def test_pgpassword_assignment_redacted():
    cap = _load()
    out = cap._scrub("PGPASSWORD=supersecret psql -h db")
    assert "supersecret" not in out


def test_url_userinfo_password_redacted():
    cap = _load()
    out = cap._scrub("git clone https://user:p4ss@example.com/r.git")
    assert "p4ss" not in out
    assert "user" in out
    assert "example.com" in out
    assert "[REDACTED]@example.com" in out


def test_plain_text_untouched():
    cap = _load()
    text = "edited config.py: set timeout=30 for the api client"
    assert cap._scrub(text) == text


def test_flag_value_does_not_cross_newline():
    cap = _load()
    out = cap._scrub("run --token\nplain next line")
    assert "plain next line" in out


def test_additional_context_has_framing_line(tmp_path):
    graph = tmp_path / "graph.jsonl"
    target = tmp_path / "app.py"
    entity = {
        "type": "entity",
        "name": "app.py",
        "entityType": "file-warning",
        "observations": ["[WARNING] fragile module"],
    }
    graph.write_text(json.dumps(entity) + "\n")
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(payload))
    # why: unique session id sidesteps the 24h /tmp warn-marker suppression.
    res = subprocess.run(
        [sys.executable,
         str(ROOT / "hooks" / "capture_tool_context.py"),
         str(input_file), str(graph)],
        capture_output=True, text=True,
        env={"CLAUDE_SESSION_ID": f"t-{uuid.uuid4().hex}", "PATH": "/usr/bin"},
    )
    out = json.loads(res.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith(
        "[easymem] Stored project memory below"
    )
    assert "fragile module" in ctx
