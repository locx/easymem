import fcntl
import importlib.util
import time
from pathlib import Path

_CLI = Path(__file__).resolve().parent.parent / "easymem-cli.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("easymem_cli", _CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_pending_defers_when_lock_unavailable(tmp_path, monkeypatch):
    cli = _load_cli()
    graph = tmp_path / "graph.jsonl"
    graph.write_text("", encoding="utf-8")
    pending = tmp_path / "graph.jsonl.pending"
    pending.write_text('{"type": "entity", "name": "x"}\n', encoding="utf-8")

    # why: simulate a busy server holding the lock so flock never succeeds,
    # and fast-forward the deadline so the timeout path fires immediately.
    monkeypatch.setattr(fcntl, "flock", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    seq = iter([0.0] + [10.0] * 100)
    monkeypatch.setattr(time, "monotonic", lambda: next(seq))

    cli._merge_pending(str(tmp_path))

    assert pending.exists()
    assert pending.read_text(encoding="utf-8").strip() != ""
    assert graph.read_text(encoding="utf-8") == ""
