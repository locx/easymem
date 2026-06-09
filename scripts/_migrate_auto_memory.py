#!/usr/bin/env python3
"""Import Claude Code's built-in auto-memory .md files into the graph.

Usage: _migrate_auto_memory.py <auto_mem_dir> <graph_jsonl>

Auto-memory lives at ~/.claude/projects/-<path-with-slashes-as-dashes>/memory/
and writes one .md per concept (plus a MEMORY.md index). We turn each one
into an entity prefixed `auto-memory:` so codebase entities don't collide.
Re-runnable: existing entity names are skipped.
"""
import json
import os
import re
import sys
import time


def _existing_names(graph_path: str) -> set:
    names = set()
    if not os.path.isfile(graph_path):
        return names
    with open(graph_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                # why: a valid-JSON non-dict (bare scalar) would raise on
                # .get — skip it like the other JSONL readers do.
                if isinstance(rec, dict) and rec.get("type") == "entity" \
                        and "name" in rec:
                    names.add(rec["name"])
            except (json.JSONDecodeError, ValueError):
                pass
    return names


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not m:
        return {}, content
    fm = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k and v:
            fm[k] = v
    return fm, content[m.end():]


def _build_observations(desc: str, body: str) -> list[str]:
    obs = []
    if desc:
        obs.append(desc)
    for line in body.strip().splitlines():
        line = line.strip()
        if line and len(line) > 3:
            obs.append(line[:200])
    return obs


def migrate(auto_mem_dir: str, graph_path: str) -> int:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = _existing_names(graph_path)
    migrated = 0
    try:
        names = sorted(os.listdir(auto_mem_dir))
    except OSError:
        return 0
    lines = []
    for fname in names:
        if not fname.endswith(".md") or fname == "MEMORY.md":
            continue
        fpath = os.path.join(auto_mem_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        fm, body = _parse_frontmatter(content)
        name = fm.get("name", os.path.splitext(fname)[0])
        obs = _build_observations(fm.get("description", ""), body)
        if not obs:
            continue
        entity_name = f"auto-memory: {name}"
        if entity_name in existing:
            continue
        entry = {
            "type": "entity",
            "name": entity_name,
            "entityType": fm.get("type", "reference"),
            "observations": obs,
            "_created": now,
            "_updated": now,
            "_migrated_from": "auto-memory",
        }
        lines.append(json.dumps(entry, separators=(",", ":")) + "\n")
        existing.add(entity_name)
        migrated += 1
    # why: one fsync after the batch instead of O(n) per-entity syncs.
    if lines:
        try:
            import fcntl as _fcntl
        except ImportError:
            _fcntl = None
        lock_path = os.path.join(os.path.dirname(graph_path), ".graph.lock")
        # why: hold .graph.lock so this append can't race a concurrent
        # server/CLI rewrite that would otherwise drop it.
        lf = open(lock_path, "a") if _fcntl is not None else None
        try:
            if lf is not None:
                _fcntl.flock(lf.fileno(), _fcntl.LOCK_EX)
            with open(graph_path, "a", encoding="utf-8") as f:
                f.writelines(lines)
                f.flush()
                os.fsync(f.fileno())
        finally:
            if lf is not None:
                try:
                    _fcntl.flock(lf.fileno(), _fcntl.LOCK_UN)
                finally:
                    lf.close()
    return migrated


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: _migrate_auto_memory.py <dir> <graph>", file=sys.stderr)
        sys.exit(2)
    n = migrate(sys.argv[1], sys.argv[2])
    if n:
        print(f"  [ok] Migrated {n} auto-memory entries into graph")
    else:
        print("  [skip] No new auto-memory entries to migrate")
