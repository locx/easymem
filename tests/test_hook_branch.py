import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "capture_tool_context", ROOT / "hooks" / "capture_tool_context.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_current_branch_detached_head(tmp_path):
    cap = _load()
    git = tmp_path / ".git"
    git.mkdir()
    sha = "a" * 40
    (git / "HEAD").write_text(sha + "\n")
    assert cap._current_branch(str(tmp_path)) == sha[:12]


def test_current_branch_uses_env(tmp_path, monkeypatch):
    cap = _load()
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/feature/x\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path.parent)
    assert cap._current_branch() == "feature/x"


def test_append_episode_warns_on_lock_fallback(tmp_path, monkeypatch, capsys):
    cap = _load()
    if cap._fcntl is None:
        return
    monkeypatch.setattr(
        cap._fcntl, "flock",
        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    graph = tmp_path / "graph.jsonl"
    cap._append_episode(str(graph), "ep", ["o"], source="episode:x")
    err = capsys.readouterr().err
    assert "pending" in err.lower() or "defer" in err.lower()
    assert (tmp_path / "graph.jsonl.pending").exists()
