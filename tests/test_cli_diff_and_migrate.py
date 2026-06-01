import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_brace_query_not_dropped_when_invalid_json():
    cli = _load("easymem_cli", "easymem-cli.py")
    # why: a brace query that isn't valid JSON must search literally, not vanish.
    assert cli._parse_positional(["{architecture notes}"]) == {
        "query": "{architecture notes}"}


def test_migrate_missing_dir_returns_zero(tmp_path):
    mig = _load("migrate_auto_memory", "scripts/_migrate_auto_memory.py")
    assert mig.migrate(str(tmp_path / "nope"),
                       str(tmp_path / "graph.jsonl")) == 0


def test_run_diff_uses_latest_revision(tmp_path, capsys):
    cli = _load("easymem_cli", "easymem-cli.py")
    cli._USE_ANSI = False
    (tmp_path / ".last-session-start").write_text("2026-01-01T00:00:00Z")
    rev1 = {"type": "entity", "name": "decision: use redis",
            "entityType": "decision", "_created": "2026-01-01T00:00:00Z",
            "_updated": "2026-05-01T00:00:00Z",
            "observations": ["Outcome: pending"]}
    rev2 = {**rev1, "_updated": "2026-06-01T00:00:00Z",
            "observations": ["Outcome: accepted"]}
    (tmp_path / "graph.jsonl").write_text(
        json.dumps(rev1) + "\n" + json.dumps(rev2) + "\n")

    cli._run_diff(str(tmp_path))
    data = json.loads(capsys.readouterr().out)
    names = [d["name"] for d in data["resolved_decisions"]]
    # why: the latest revision (accepted) must drive resolution, not the first.
    assert "decision: use redis" in names
