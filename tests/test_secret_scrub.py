import importlib.util
import json
import re
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


# why: the scrub patterns are hand-copied into three files; pin them so a
# change to one copy that isn't mirrored fails CI instead of silently leaking.
_CANON = ROOT / "semantic_server" / "text.py"
_HOOK = ROOT / "hooks" / "capture_tool_context.py"
_SHELL = ROOT / "import-easymem.sh"
_FRAG_RE = re.compile(r'r"([^"]*)"')


def _scrub_patterns(path):
    text = path.read_text(encoding="utf-8")

    def block(name):
        start = text.index(f"{name} = re.compile(")
        end = text.index("\n)", start)
        return "".join(_FRAG_RE.findall(text[start:end]))

    return block("_SECRET_RE"), block("_URL_CRED_RE")


def test_scrub_pattern_copies_in_sync():
    canon = _scrub_patterns(_CANON)
    # why: guard a vacuous pass — if a copy switched r-string quoting the
    # extractor would return "" for all and equality would hold trivially.
    assert canon[0] and canon[1]
    assert _scrub_patterns(_HOOK) == canon
    assert _scrub_patterns(_SHELL) == canon


_SECRET_CORPUS = [
    ("access key AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    ("token ghp_%s end" % ("a" * 36), "ghp_" + "a" * 36),
    ("key sk-%s end" % ("b" * 32), "sk-" + "b" * 32),
    ("slack xoxb-%s end" % ("1" * 24), "xoxb-" + "1" * 24),
    ("auth Bearer %s end" % ("c" * 40), "c" * 40),
    ("body -----BEGIN RSA PRIVATE KEY----- tail",
     "-----BEGIN RSA PRIVATE KEY-----"),
    ("run --password=hunter2 db", "hunter2"),
    ("export FOO_TOKEN=abc123def456 && go", "abc123def456"),
    ("clone https://user:p4ss@example.com/r.git", "p4ss"),
]


def test_python_scrub_copies_redact_identically():
    from semantic_server.text import scrub_secrets
    cap = _load()
    for text, secret in _SECRET_CORPUS:
        canon = scrub_secrets(text)
        assert cap._scrub(text) == canon, secret
        assert secret not in canon, secret
        assert "[REDACTED]" in canon, secret
