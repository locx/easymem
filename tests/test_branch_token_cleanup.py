import importlib.util
from pathlib import Path

import maintenance

ROOT = Path(__file__).resolve().parents[1]


def _load_capture():
    spec = importlib.util.spec_from_file_location(
        "capture_tool_context", ROOT / "hooks" / "capture_tool_context.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_get_branch_detached_head(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    sha = "a" * 40
    (git / "HEAD").write_text(sha + "\n")
    assert maintenance.get_branch(cwd=str(tmp_path)) == sha[:12]


def test_get_branch_normal_branch(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/feature/x\n")
    assert maintenance.get_branch(cwd=str(tmp_path)) == "feature/x"


def test_get_branch_unknown_on_missing(tmp_path):
    assert maintenance.get_branch(cwd=str(tmp_path)) == "unknown"


def test_safe_token_sanitizes_and_truncates():
    cap = _load_capture()
    assert cap._safe_token("ab/cd .e") == "ab_cd__e"
    assert cap._safe_token("keep_-09AZ") == "keep_-09AZ"
    assert len(cap._safe_token("x" * 200)) == 64
