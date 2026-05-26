"""File-level code-structure indexer.

Emits file:<relative-path> entities into the easymem knowledge graph
so search returns code alongside conversation memory.
"""
from __future__ import annotations

import ast
import os
import re

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "ts",
    ".tsx": "ts",
    ".js": "ts",
    ".jsx": "ts",
    ".mjs": "ts",
    ".cjs": "ts",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
}


def detect_language(path: str) -> str | None:
    _, ext = os.path.splitext(path)
    return _EXT_TO_LANG.get(ext.lower())


# why: top-level only — leading whitespace would scrape nested defs/methods.
_PY_DEF_OR_CLASS = re.compile(r"^(?:def|class)\s+([A-Za-z_]\w*)",
                               re.MULTILINE)
_PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import\s+|import\s+([\w.]+))",
    re.MULTILINE,
)
_PY_DOCSTRING = re.compile(
    r'^\s*[ru]?(?:"""|\'\'\')(.*?)(?:"""|\'\'\')',
    re.DOTALL,
)

_TS_EXPORT = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
    r"(?:function|class|const|let|var|interface|type|enum)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_TS_IMPORT = re.compile(
    r"^\s*import\s+(?:.*?\s+from\s+)?['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
_TS_DOCSTRING = re.compile(r"^\s*/\*\*\s*(.*?)\s*\*/", re.DOTALL)

# why: TS re-exports — `export { foo } from "./mod"` exposes foo AND counts
# as an import of "./mod".
_TS_REEXPORT = re.compile(
    r"^\s*export\s*\{\s*([^}]+)\s*\}\s*from\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# why: TS aliased exports (`export { foo as bar }`) expose `bar`, not `foo`.
_TS_EXPORT_ALIAS = re.compile(r"\b([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)")

_GO_FUNC = re.compile(
    r"^func\s+(?:\([^)]+\)\s+)?([A-Za-z_]\w*)", re.MULTILINE,
)
_GO_IMPORT_SINGLE = re.compile(r'^\s*import\s+"([^"]+)"', re.MULTILINE)
_GO_IMPORT_BLOCK = re.compile(
    r'import\s*\(([^)]+)\)', re.DOTALL,
)

# why: rust public surface only; private items aren't "exports".
_RUST_PUB = re.compile(
    r"^\s*pub\s+(?:fn|struct|enum|trait|mod|const)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)
_RUST_USE = re.compile(r"^\s*use\s+([\w:]+)", re.MULTILINE)

_RUBY_DEF = re.compile(
    r"^\s*(?:def\s+(?:self\.)?|class\s+|module\s+)"
    r"([A-Za-z_]\w*)",
    re.MULTILINE,
)
_RUBY_REQUIRE = re.compile(
    r"""^\s*require(?:_relative)?\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def _first_match_group(rx: re.Pattern, text: str, default: str = "") -> str:
    m = rx.search(text)
    return m.group(1).strip() if m else default


def _extract_python(text: str) -> dict:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {"exports": [], "imports": [], "docstring": "", "kinds": [],
                "doc_lines": []}
    exports: list[str] = []
    kinds: list[str] = []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                exports.append(node.name)
                kinds.append("function")
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                exports.append(node.name)
                kinds.append("class")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                mod = ("." * node.level) + mod
            imports.append(mod)
    doc = ast.get_docstring(tree) or ""
    doc_lines = [ln.strip() for ln in doc.splitlines() if ln.strip()]
    first = doc_lines[0] if doc_lines else ""
    return {"exports": exports, "imports": imports,
            "docstring": first, "kinds": kinds,
            "doc_lines": doc_lines}


def _extract_ts(text: str) -> dict:
    exports = [m.group(1) for m in _TS_EXPORT.finditer(text)]
    imports = [m.group(1) for m in _TS_IMPORT.finditer(text)]
    for m in _TS_REEXPORT.finditer(text):
        clause, src = m.group(1), m.group(2)
        imports.append(src)
        for piece in clause.split(","):
            piece = piece.strip()
            if not piece:
                continue
            alias = _TS_EXPORT_ALIAS.search(piece)
            if alias:
                exports.append(alias.group(2))
            else:
                exports.append(piece)
    # why: ts module JSDoc is /** ... */ — strip leading `*` per line.
    doc = _first_match_group(_TS_DOCSTRING, text)
    doc_lines = [ln.strip().lstrip("*").strip()
                 for ln in doc.splitlines() if ln.strip()]
    first = doc_lines[0] if doc_lines else ""
    return {"exports": exports, "imports": imports,
            "docstring": first,
            "kinds": ["function"] * len(exports),
            "doc_lines": doc_lines}


def _extract_go(text: str) -> dict:
    exports = [m.group(1) for m in _GO_FUNC.finditer(text)]
    imports = [m.group(1) for m in _GO_IMPORT_SINGLE.finditer(text)]
    for block in _GO_IMPORT_BLOCK.finditer(text):
        for line in block.group(1).splitlines():
            s = line.strip().strip('"').strip()
            if s and not s.startswith("//"):
                imports.append(s)
    return {"exports": exports, "imports": imports, "docstring": "",
            "kinds": ["function"] * len(exports), "doc_lines": []}


def _extract_rust(text: str) -> dict:
    exports = [m.group(1) for m in _RUST_PUB.finditer(text)]
    imports = [m.group(1) for m in _RUST_USE.finditer(text)]
    return {"exports": exports, "imports": imports, "docstring": "",
            "kinds": ["function"] * len(exports), "doc_lines": []}


def _extract_ruby(text: str) -> dict:
    exports = [m.group(1) for m in _RUBY_DEF.finditer(text)]
    imports = [m.group(1) for m in _RUBY_REQUIRE.finditer(text)]
    return {"exports": exports, "imports": imports, "docstring": "",
            "kinds": ["function"] * len(exports), "doc_lines": []}


_EXTRACTORS = {
    "python": _extract_python,
    "ts": _extract_ts,
    "go": _extract_go,
    "rust": _extract_rust,
    "ruby": _extract_ruby,
}


def extract(text: str, lang: str) -> dict:
    fn = _EXTRACTORS.get(lang)
    if not fn:
        return {"exports": [], "imports": [], "docstring": "",
                "kinds": [], "doc_lines": []}
    return fn(text)



from pathlib import Path

_TS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_PY_EXTS = (".py", ".pyi")


def _resolve_with_exts(base: Path, exts: tuple[str, ...]) -> Path | None:
    if base.is_file():
        return base
    for ext in exts:
        cand = base.with_suffix(ext) if base.suffix else (
            base.parent / (base.name + ext)
        )
        if cand.is_file():
            return cand
        # why: TS/JS resolves bare dir imports to <dir>/index.<ext>
        idx = base / f"index{ext}"
        if idx.is_file():
            return idx
    return None


def resolve_import(import_str: str, lang: str,
                   file_path: str, project_root: str) -> str | None:
    if not import_str:
        return None
    root = Path(project_root).resolve()
    here = Path(file_path).resolve()
    if lang == "python":
        if import_str.startswith("."):
            # why: a leading dot is one parent per dot above the first
            n_dots = len(import_str) - len(import_str.lstrip("."))
            rest = import_str[n_dots:]
            cur = here.parent
            for _ in range(n_dots - 1):
                cur = cur.parent
            base = cur / rest.replace(".", "/")
        else:
            base = root / import_str.replace(".", "/")
        hit = _resolve_with_exts(base, _PY_EXTS)
        if not hit:
            # why: package imports resolve to <pkg>/__init__.py
            init = base / "__init__.py"
            if init.is_file():
                hit = init
    elif lang == "ts":
        if not (import_str.startswith(".") or import_str.startswith("/")):
            return None
        base = (here.parent / import_str).resolve()
        hit = _resolve_with_exts(base, _TS_EXTS)
    else:
        return None
    if not hit:
        return None
    try:
        rel = hit.relative_to(root)
    except ValueError:
        return None
    return rel.as_posix()


from dataclasses import dataclass

MAX_FILE_BYTES = 1024 * 1024  # 1 MiB

DEFAULT_EXCLUDES: frozenset[str] = frozenset({
    ".git", ".easymem", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "dist", "build", "target", "out", ".next",
    ".cache", ".tox", "coverage", ".coverage",
})


@dataclass(frozen=True)
class ScannedFile:
    rel_path: str
    lang: str
    text: str


def scan_project(root, excludes: frozenset[str] | set[str] | None = None):
    root_path = Path(root).resolve()
    excl = excludes if excludes is not None else DEFAULT_EXCLUDES
    for dirpath, dirnames, filenames in os.walk(root_path):
        # why: prune dirnames in-place to skip excluded subtrees
        dirnames[:] = [d for d in dirnames if d not in excl
                       and not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            full = Path(dirpath) / fname
            try:
                rel = full.relative_to(root_path).as_posix()
            except ValueError:
                continue
            lang = detect_language(rel)
            if not lang:
                continue
            try:
                sz = full.stat().st_size
            except OSError:
                continue
            if sz > MAX_FILE_BYTES:
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            yield ScannedFile(rel_path=rel, lang=lang, text=text)


from datetime import datetime, timezone

from .graph import GraphLock, rewrite_graph
from .io_utils import partition_graph


_CODE_SOURCE_PREFIX = "code:scan:"


def _build_entity(sf: ScannedFile, now_iso: str) -> dict:
    info = extract(sf.text, sf.lang)
    obs: list[str] = [f"lang: {sf.lang}"]
    # why: one observation per fact mirrors the project's existing pattern
    # (e.g. workflow extractor emits one `event: ...` per event).
    for name in info["exports"][:50]:
        obs.append(f"export: {name}")
    for imp in info["imports"][:50]:
        obs.append(f"import: {imp}")
    if info.get("doc_lines"):
        for line in info["doc_lines"][:20]:  # cap to keep entities readable
            obs.append(f"doc: {line}")
    elif info["docstring"]:
        obs.append(f"module-doc: {info['docstring']}")
    return {
        "name": f"file:{sf.rel_path}",
        "entityType": "file",
        "observations": obs,
        "_source": _CODE_SOURCE_PREFIX + now_iso,
    }


def _build_symbol_entities(sf: ScannedFile, now_iso: str) -> list[dict]:
    info = extract(sf.text, sf.lang)
    out: list[dict] = []
    for name, kind in zip(info["exports"], info.get("kinds") or []):
        out.append({
            "name": f"{kind}:{sf.rel_path}::{name}",
            "entityType": kind,
            "observations": [
                f"lang: {sf.lang}",
                f"defined-in: {sf.rel_path}",
                f"name: {name}",
            ],
            "_source": _CODE_SOURCE_PREFIX + now_iso,
        })
    return out


def _build_defined_in_relations(sf: ScannedFile) -> list[dict]:
    info = extract(sf.text, sf.lang)
    rels: list[dict] = []
    for name, kind in zip(info["exports"], info.get("kinds") or []):
        rels.append({
            "from": f"{kind}:{sf.rel_path}::{name}",
            "to": f"file:{sf.rel_path}",
            "relationType": "defined_in",
        })
    return rels


def _build_relations(sf: ScannedFile, project_root: str) -> list[dict]:
    info = extract(sf.text, sf.lang)
    full = str(Path(project_root) / sf.rel_path)
    rels: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for imp in info["imports"]:
        target = resolve_import(imp, sf.lang, full, project_root)
        if not target:
            continue
        key = (sf.rel_path, target)
        if key in seen:
            continue
        seen.add(key)
        rels.append({
            "from": f"file:{sf.rel_path}",
            "to": f"file:{target}",
            "relationType": "imports",
        })
    return rels


def index_project(memory_dir: str, project_root: str,
                  excludes: frozenset[str] | None = None) -> dict:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen_names: set[str] = set()
    new_entities: list[dict] = []
    new_rels: list[dict] = []
    for sf in scan_project(project_root, excludes):
        ent = _build_entity(sf, now_iso)
        new_entities.append(ent)
        seen_names.add(ent["name"])
        sym_ents = _build_symbol_entities(sf, now_iso)
        new_entities.extend(sym_ents)
        seen_names.update(e["name"] for e in sym_ents)
        new_rels.extend(_build_relations(sf, project_root))
        new_rels.extend(_build_defined_in_relations(sf))

    graph_path = os.path.join(memory_dir, "graph.jsonl")
    # why: lock across partition+rewrite so a concurrent server/maintenance
    # append between the two operations can't be clobbered.
    with GraphLock(memory_dir) as _lock:
        if not _lock.acquired:
            return {
                "indexed": 0,
                "removed": 0,
                "relations": 0,
                "symbols": 0,
                "error": "graph lock timeout",
            }
        raw_entities, raw_rels, _ = partition_graph(graph_path)

        # why: raw partition (not load_graph_entities) preserves _source for
        # session tagging; indexer owns every file:/function:/class: entity.
        OWNED_PREFIXES = ("file:", "function:", "class:")
        kept: dict[str, dict] = {}
        removed = 0
        dropped_owned: set[str] = set()
        for obj in raw_entities:
            name = obj.get("name", "")
            if any(name.startswith(p) for p in OWNED_PREFIXES):
                if name not in seen_names:
                    removed += 1
                    dropped_owned.add(name)
                    continue
                # will be replaced by new_entities below
                continue
            kept[name] = {
                k: v for k, v in obj.items() if k != "name" and k != "type"
            }
        for ent in new_entities:
            kept[ent["name"]] = {k: v for k, v in ent.items() if k != "name"}

        # why: drop owned-type relations AND any inbound relation whose endpoint
        # references an entity we just dropped — otherwise dangling refs leak.
        _OWNED_REL_TYPES = ("imports", "defined_in")
        kept_rels: list[dict] = []
        for r in raw_rels:
            if r.get("relationType") in _OWNED_REL_TYPES:
                continue
            if r.get("from") in dropped_owned or r.get("to") in dropped_owned:
                continue
            kept_rels.append({k: v for k, v in r.items() if k != "type"})
        kept_rels.extend(new_rels)

        rewrite_graph(memory_dir, kept, kept_rels, _lock_held=True)
    n_files = sum(1 for e in new_entities if e["name"].startswith("file:"))
    n_symbols = len(new_entities) - n_files
    return {
        "indexed": n_files,
        "removed": removed,
        "relations": len(new_rels),
        "symbols": n_symbols,
    }


def code_scan_is_stale(memory_dir: str, project_root: str) -> bool:
    stamp = Path(memory_dir) / "code-stamp"
    try:
        stamp_mtime = stamp.stat().st_mtime
    except OSError:
        return True
    root = Path(project_root).resolve()
    excl = DEFAULT_EXCLUDES
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excl
                       and not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            if detect_language(fname) is None:
                continue
            try:
                if (Path(dirpath) / fname).stat().st_mtime > stamp_mtime:
                    return True
            except OSError:
                continue
    return False


def touch_code_stamp(memory_dir: str) -> None:
    stamp = Path(memory_dir) / "code-stamp"
    stamp.write_text("")
