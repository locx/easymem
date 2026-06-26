"""A planted secret must never land in graph.jsonl via any write path."""
from semantic_server.text import scrub_secrets
from semantic_server.tools import (
    add_observations,
    create_decision,
    create_entities,
)

_SECRETS = [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_0123456789abcdefghijABCDEFGHIJ0123",
    "sk-0123456789abcdefghijABCDEFGHIJ",
    "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI0123456789EXAMPLEKEY",
    "postgres://user:hunter2@localhost:5432/db",
]


def _graph_text(memory_dir):
    with open(f"{memory_dir}/graph.jsonl", encoding="utf-8") as f:
        return f.read()


def _assert_clean(text):
    for s in _SECRETS:
        # the URL case keeps scheme/user/host; only the password is redacted
        secret_part = "hunter2" if s.startswith("postgres://") else s
        assert secret_part not in text, f"leaked: {secret_part}"


def test_scrub_secrets_unit():
    for s in _SECRETS:
        out = scrub_secrets(s)
        if s.startswith("postgres://"):
            assert "hunter2" not in out and "localhost" in out
        else:
            assert "[REDACTED]" in out


def test_create_entities_scrubs(tmp_path):
    md = str(tmp_path / ".easymem")
    import os
    os.makedirs(md, exist_ok=True)
    create_entities(
        [{"name": "cfg", "entityType": "file",
          "observations": _SECRETS}], md)
    _assert_clean(_graph_text(md))


def test_add_observations_scrubs(tmp_path):
    md = str(tmp_path / ".easymem")
    import os
    os.makedirs(md, exist_ok=True)
    create_entities([{"name": "cfg", "observations": ["seed"]}], md)
    add_observations("cfg", _SECRETS, md)
    _assert_clean(_graph_text(md))


def test_create_decision_scrubs(tmp_path):
    md = str(tmp_path / ".easymem")
    import os
    os.makedirs(md, exist_ok=True)
    create_decision({
        "title": "use a token",
        "rationale": "key is ghp_0123456789abcdefghijABCDEFGHIJ0123",
        "alternatives": ["postgres://user:hunter2@localhost:5432/db"],
    }, md)
    _assert_clean(_graph_text(md))
