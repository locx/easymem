from semantic_server.code_index import detect_language


def test_python_extensions():
    assert detect_language("src/auth.py") == "python"
    assert detect_language("a/b/x.pyi") == "python"


def test_typescript_javascript_family():
    assert detect_language("ui/x.ts") == "ts"
    assert detect_language("ui/x.tsx") == "ts"
    assert detect_language("ui/x.js") == "ts"
    assert detect_language("ui/x.jsx") == "ts"
    assert detect_language("ui/x.mjs") == "ts"


def test_go_rust_ruby():
    assert detect_language("a/b.go") == "go"
    assert detect_language("a/b.rs") == "rust"
    assert detect_language("a/b.rb") == "ruby"


def test_unknown_returns_none():
    assert detect_language("README.md") is None
    assert detect_language("photo.png") is None
    assert detect_language("data.json") is None
    assert detect_language("no_extension") is None


from semantic_server.code_index import extract


def test_python_exports_and_imports():
    src = '''"""Auth helpers."""
import os
from .session import SessionStore

def login(user):
    pass

class AuthService:
    pass

def _private():
    pass
'''
    out = extract(src, "python")
    assert "login" in out["exports"]
    assert "AuthService" in out["exports"]
    assert "_private" not in out["exports"]
    assert "os" in out["imports"]
    assert ".session" in out["imports"]
    assert "Auth helpers." in out["docstring"]


def test_ts_exports_and_imports():
    src = '''/** UI auth wrapper. */
import { Session } from "./session";
import React from "react";

export function login(): void {}
export class AuthPanel {}
export const TOKEN_KEY = "k";
function helper() {}
'''
    out = extract(src, "ts")
    assert "login" in out["exports"]
    assert "AuthPanel" in out["exports"]
    assert "TOKEN_KEY" in out["exports"]
    assert "helper" not in out["exports"]
    assert "./session" in out["imports"]
    assert "react" in out["imports"]


def test_go_exports():
    src = '''package auth

import "fmt"

func Login() {}
func helper() {}
'''
    out = extract(src, "go")
    assert "Login" in out["exports"]
    assert "helper" in out["exports"]
    assert "fmt" in out["imports"]


def test_rust_exports():
    src = '''//! Auth module.
use crate::session::Store;

pub fn login() {}
pub struct AuthService;
fn private_helper() {}
'''
    out = extract(src, "rust")
    assert "login" in out["exports"]
    assert "AuthService" in out["exports"]
    assert "private_helper" not in out["exports"]
    assert "crate::session::Store" in out["imports"]


def test_ruby_exports():
    src = '''require "json"
module Auth
  def self.login; end
  class Service; end
end
'''
    out = extract(src, "ruby")
    assert "Auth" in out["exports"]
    assert "Service" in out["exports"]
    assert "json" in out["imports"]


def test_unknown_lang_returns_empty():
    out = extract("anything", "other")
    assert out == {"exports": [], "imports": [], "docstring": "",
                   "kinds": [], "doc_lines": []}


from semantic_server.code_index import resolve_import
from pathlib import Path


def test_python_relative_dot_import(tmp_path):
    root = tmp_path
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "session.py").write_text("")
    (root / "pkg" / "auth.py").write_text("")
    out = resolve_import(".session", "python",
                         str(root / "pkg" / "auth.py"), str(root))
    assert out == "pkg/session.py"


def test_python_absolute_package_import(tmp_path):
    root = tmp_path
    (root / "src").mkdir()
    (root / "src" / "__init__.py").write_text("")
    (root / "src" / "db.py").write_text("")
    out = resolve_import("src.db", "python",
                         str(root / "main.py"), str(root))
    assert out == "src/db.py"


def test_python_stdlib_unresolved(tmp_path):
    out = resolve_import("os", "python",
                         str(tmp_path / "x.py"), str(tmp_path))
    assert out is None


def test_ts_relative(tmp_path):
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "session.ts").write_text("")
    (tmp_path / "ui" / "app.ts").write_text("")
    out = resolve_import("./session", "ts",
                         str(tmp_path / "ui" / "app.ts"), str(tmp_path))
    assert out == "ui/session.ts"


def test_ts_index_resolution(tmp_path):
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "utils").mkdir()
    (tmp_path / "ui" / "utils" / "index.ts").write_text("")
    (tmp_path / "ui" / "app.ts").write_text("")
    out = resolve_import("./utils", "ts",
                         str(tmp_path / "ui" / "app.ts"), str(tmp_path))
    assert out == "ui/utils/index.ts"


def test_ts_node_module_unresolved(tmp_path):
    out = resolve_import("react", "ts",
                         str(tmp_path / "x.tsx"), str(tmp_path))
    assert out is None


def test_python_package_init_resolution(tmp_path):
    root = tmp_path
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "sub" / "__init__.py").write_text("")
    out = resolve_import("pkg.sub", "python",
                         str(root / "main.py"), str(root))
    assert out == "pkg/sub/__init__.py"


from semantic_server.code_index import scan_project, DEFAULT_EXCLUDES


def _w(p: Path, content: str = "") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_scan_finds_source_files(tmp_path):
    _w(tmp_path / "src" / "auth.py", "def x():\n    pass\n")
    _w(tmp_path / "ui" / "app.ts", "export const X = 1;\n")
    _w(tmp_path / "README.md", "# notes")
    files = list(scan_project(tmp_path))
    paths = sorted(f.rel_path for f in files)
    assert paths == ["src/auth.py", "ui/app.ts"]


def test_scan_respects_default_excludes(tmp_path):
    _w(tmp_path / "src" / "a.py", "")
    _w(tmp_path / "node_modules" / "x" / "b.js", "")
    _w(tmp_path / "__pycache__" / "x.py", "")
    _w(tmp_path / ".venv" / "lib" / "x.py", "")
    paths = sorted(f.rel_path for f in scan_project(tmp_path))
    assert paths == ["src/a.py"]


def test_scan_skips_files_over_max_bytes(tmp_path):
    big = "x" * (1024 * 1024 + 1)
    _w(tmp_path / "big.py", big)
    _w(tmp_path / "small.py", "y = 1\n")
    paths = sorted(f.rel_path for f in scan_project(tmp_path))
    assert paths == ["small.py"]


def test_scan_respects_extra_excludes(tmp_path):
    _w(tmp_path / "src" / "a.py", "")
    _w(tmp_path / "vendor" / "b.py", "")
    paths = sorted(f.rel_path for f in scan_project(
        tmp_path, excludes=DEFAULT_EXCLUDES | {"vendor"},
    ))
    assert paths == ["src/a.py"]


import json
from semantic_server.code_index import index_project


def test_index_project_writes_file_entities(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text("")
    _w(proj / "src" / "auth.py",
       '"""Auth."""\nfrom .session import S\ndef login(): pass\n')
    _w(proj / "src" / "session.py", '"""Sessions."""\n')
    result = index_project(str(mem), str(proj))
    assert result["indexed"] == 2
    assert result["removed"] == 0
    assert result["relations"] >= 1
    lines = [json.loads(ln) for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    names = {ln["name"] for ln in lines if ln.get("type") == "entity"}
    assert "file:src/auth.py" in names
    assert "file:src/session.py" in names


def test_index_project_attaches_observations(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text("")
    _w(proj / "auth.py",
       '"""Auth helpers."""\ndef login(): pass\nclass Svc: pass\n')
    index_project(str(mem), str(proj))
    lines = [json.loads(ln) for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    ent = next(ln for ln in lines
               if ln.get("name") == "file:auth.py")
    obs = ent.get("observations") or []
    assert "lang: python" in obs
    assert "export: login" in obs
    assert "export: Svc" in obs
    assert any(o.startswith("doc: Auth helpers.") for o in obs)
    assert ent.get("_source", "").startswith("code:scan:")


def test_index_project_emits_import_relations(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text("")
    _w(proj / "a.py", "from b import x\n")
    _w(proj / "b.py", "x = 1\n")
    index_project(str(mem), str(proj))
    lines = [json.loads(ln) for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    rels = [ln for ln in lines
            if ln.get("type") == "relation"
            and ln.get("relationType") == "imports"]
    assert any(r["from"] == "file:a.py" and r["to"] == "file:b.py"
               for r in rels)


def test_index_project_sweeps_deleted_files(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text("")
    _w(proj / "keeper.py", "x = 1\n")
    gone = _w(proj / "gone.py", "y = 1\n")
    first = index_project(str(mem), str(proj))
    assert first["indexed"] == 2
    gone.unlink()
    second = index_project(str(mem), str(proj))
    assert second["indexed"] == 1
    assert second["removed"] == 1
    lines = [json.loads(ln) for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    names = {ln["name"] for ln in lines if ln.get("type") == "entity"}
    assert "file:keeper.py" in names
    assert "file:gone.py" not in names


import subprocess
import sys
import time


def test_cli_index_code_runs(tmp_path):
    proj = tmp_path
    (proj / ".easymem").mkdir()
    (proj / ".easymem" / "graph.jsonl").write_text("")
    _w(proj / "src" / "x.py", "def hi(): pass\n")
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, str(root / "easymem-cli.py"),
         "index-code", str(proj)],
        capture_output=True, text=True, check=True, cwd=proj,
    )
    assert "indexed" in out.stdout.lower()
    lines = (proj / ".easymem" / "graph.jsonl").read_text().splitlines()
    assert any("file:src/x.py" in ln for ln in lines)


def test_maintenance_runs_code_scan_when_stale(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    (mem / "graph.jsonl").write_text(
        '{"type":"entity","name":"Seed","entityType":"x",'
        '"observations":["seed"],'
        f'"_created":"{now}","_updated":"{now}"' + "}\n"
    )
    _w(proj / "main.py", "def boot(): pass\n")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(root / "maintenance.py"),
         str(proj), "--force"],
        check=True, capture_output=True,
    )
    lines = [ln for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    assert any('"file:main.py"' in ln for ln in lines), (
        f"expected file entity in graph; got: {lines}"
    )
    assert (mem / "code-stamp").exists()


def test_maintenance_skips_code_scan_when_fresh(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    (mem / "graph.jsonl").write_text(
        '{"type":"entity","name":"Seed","entityType":"x",'
        '"observations":["seed"],'
        f'"_created":"{now}","_updated":"{now}"' + "}\n"
    )
    _w(proj / "main.py", "def boot(): pass\n")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(root / "maintenance.py"),
         str(proj), "--force"],
        check=True, capture_output=True,
    )
    stamp_mtime_1 = (mem / "code-stamp").stat().st_mtime
    time.sleep(0.05)
    subprocess.run(
        [sys.executable, str(root / "maintenance.py"),
         str(proj), "--force"],
        check=True, capture_output=True,
    )
    stamp_mtime_2 = (mem / "code-stamp").stat().st_mtime
    assert stamp_mtime_2 == stamp_mtime_1


def test_python_ast_handles_decorators():
    src = '''
@staticmethod
def helper(): pass

@app.route("/x")
async def login(): pass
'''
    out = extract(src, "python")
    assert "login" in out["exports"]
    assert "helper" in out["exports"]


def test_python_ast_handles_multiline_docstring():
    src = '''"""Auth module.

Provides session helpers.
"""
def x(): pass
'''
    out = extract(src, "python")
    assert "Auth module." in out["docstring"]


def test_python_ast_handles_class_methods_not_exported():
    src = '''
class Service:
    def method(self): pass

def top(): pass
'''
    out = extract(src, "python")
    assert "Service" in out["exports"]
    assert "top" in out["exports"]
    assert "method" not in out["exports"]


def test_python_ast_invalid_source_returns_empty():
    src = "def(\n"
    out = extract(src, "python")
    assert out == {"exports": [], "imports": [], "docstring": "", "kinds": [],
                   "doc_lines": []}


def test_ts_reexport():
    src = 'export { login } from "./auth";\n'
    out = extract(src, "ts")
    assert "login" in out["exports"]
    assert "./auth" in out["imports"]


def test_ts_export_alias():
    src = 'export { foo as bar } from "./mod";\n'
    out = extract(src, "ts")
    assert "bar" in out["exports"]


def test_ts_default_export_named():
    src = 'export default function login() {}\n'
    out = extract(src, "ts")
    assert "login" in out["exports"]


def test_ts_default_export_anon():
    src = 'export default function () {}\n'
    out = extract(src, "ts")
    # Anonymous default export contributes nothing nameable.
    assert out["exports"] == []


def test_index_project_emits_symbol_entities(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text("")
    _w(proj / "auth.py",
       '"""Auth helpers."""\ndef login(): pass\nclass Svc: pass\n')
    index_project(str(mem), str(proj))
    lines = [json.loads(ln) for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    names = {ln["name"] for ln in lines if ln.get("type") == "entity"}
    assert "file:auth.py" in names
    assert "function:auth.py::login" in names
    assert "class:auth.py::Svc" in names


def test_index_project_emits_defined_in_relations(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text("")
    _w(proj / "auth.py", "def login(): pass\n")
    index_project(str(mem), str(proj))
    lines = [json.loads(ln) for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    rels = [ln for ln in lines
            if ln.get("type") == "relation"
            and ln.get("relationType") == "defined_in"]
    assert any(r["from"] == "function:auth.py::login"
               and r["to"] == "file:auth.py" for r in rels)


def test_index_project_sweeps_symbol_entities(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text("")
    f = _w(proj / "auth.py", "def login(): pass\n")
    index_project(str(mem), str(proj))
    f.write_text("def signout(): pass\n")
    index_project(str(mem), str(proj))
    lines = [json.loads(ln) for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    names = {ln["name"] for ln in lines if ln.get("type") == "entity"}
    assert "function:auth.py::signout" in names
    assert "function:auth.py::login" not in names


def test_index_project_emits_multi_line_doc_observations(tmp_path):
    proj = tmp_path
    mem = proj / ".easymem"
    mem.mkdir()
    (mem / "graph.jsonl").write_text("")
    _w(proj / "auth.py",
       '"""Auth module.\n\nValidates session tokens via JWT.\n'
       'Refresh flow is in session.refresh().\n"""\ndef login(): pass\n')
    index_project(str(mem), str(proj))
    lines = [json.loads(ln) for ln in
             (mem / "graph.jsonl").read_text().splitlines() if ln.strip()]
    ent = next(ln for ln in lines if ln.get("name") == "file:auth.py")
    obs = ent.get("observations") or []
    doc_lines = [o for o in obs if o.startswith("doc: ")]
    assert any("Validates session tokens via JWT" in o for o in doc_lines)
    assert any("Refresh flow" in o for o in doc_lines)
