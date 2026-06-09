#!/usr/bin/env python3
"""CLI for the knowledge graph memory system.

Usage: python3 easymem-cli.py [--easymem-dir DIR] <command> [args]

Commands: search, recall, write, decide, remove, status, doctor,
          rebuild, diff.
"""
import json
import os
import sys
import time
from contextlib import contextmanager

# Add script's own directory to path
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

_USE_ANSI = (
    sys.stdout.isatty()
    and not os.environ.get("NO_COLOR")
    and os.environ.get("TERM", "") != "dumb"
)

_READ_ONLY_CMDS = {"search", "recall", "status", "diff", "doctor"}


def _resolve_memory_dir(argv):
    """Extract memory_dir from --easymem-dir flag (both = and space forms), env, or cwd."""
    md = None
    cleaned = []
    i = 0
    while i < len(argv):
        if argv[i] == "--easymem-dir" and i + 1 < len(argv):
            md = argv[i + 1]
            i += 2
        elif argv[i].startswith("--easymem-dir="):
            md = argv[i].split("=", 1)[1]
            i += 1
        else:
            cleaned.append(argv[i])
            i += 1
    if md is None:
        md = os.environ.get("EASYMEM_DIR")
    if md is None:
        md = os.path.join(os.getcwd(), ".easymem")
    return md, cleaned


def _usage(stream=sys.stderr, exit_code=1):
    print(
        "Usage: easymem [--easymem-dir DIR] "
        "<command> [args]\n"
        "\nCommands:\n"
        "  search <query>          "
        "Search knowledge graph\n"
        "  recall <query>          "
        "Search + 1-hop graph neighbors\n"
        "  write  '<json>'         "
        "Create graph entities/relations/obs\n"
        "  decide '<json>'         "
        "Create or resolve a decision\n"
        "  remove '<json>'         "
        "Delete graph entities/observations\n"
        "  status                  "
        "Graph health + diagnostics\n"
        "  doctor                  "
        "Deep health check\n"
        "  rebuild                 "
        "Rebuild TF-IDF index\n"
        "  diff                    "
        "Changes since last session\n"
        "  aliases <op> [args]     "
        "Manage synonym groups (add|list|remove)\n"
        "  slots <op> [args]       "
        "Manage pinned slots (get|set|list)\n"
        "  index-code [PATH]       "
        "Index project source files into the graph\n"
        "  nudge suppress          "
        "Stop SessionStart nudges for the current project\n"
        "  export [OUTPUT]         "
        "Export graph to a portable JSON bundle\n"
        "  import <BUNDLE>         "
        "Merge a bundle into the project graph\n"
        "  help                    "
        "Show this message\n"
        "\nFlags:\n"
        "  --easymem-dir DIR        "
        "Override memory directory\n"
        "  --mode MODE             "
        "Search mode: semantic|temporal|graph\n"
        "  --since DATE            "
        "Filter entities updated since DATE\n"
        "  --depth N               "
        "Graph traversal depth (default 2)\n"
        "  --type TYPE             "
        "Filter by entity type\n"
        "  --compact               "
        "Compact output (fewer observations)\n"
        "  --top-k N               "
        "Max results to return (default 5)",
        file=stream,
    )
    sys.exit(exit_code)


def _parse_positional(args):
    """Parse positional args into a tool_args dict.

    Supports flags before or after positional values:
      search auth service     -> {"query": "auth service"}
      search auth --compact   -> {"query": "auth", "compact": true}
    Falls back to JSON parsing for complex args.
    """
    if not args:
        return {}
    first = args[0]
    if first.startswith('{') or first.startswith('['):
        try:
            parsed = json.loads(first)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            # why: a brace/bracket query that isn't valid JSON is a normal
            # search string; fall through instead of dropping it.
            pass
    # flag -> (result_key, cast); str never raises so warning path
    # is int-only.
    flag_spec = {
        "--top-k": ("top_k", int),
        "--mode": ("mode", str),
        "--since": ("since", str),
        "--until": ("until", str),
        "--type": ("entity_type", str),
        # why: handlers read max_depth (graph mode) — the prior 'depth' key
        # was silently dropped because nothing consumed it.
        "--depth": ("max_depth", int),
        "--max-per-session": ("max_per_session", int),
        "--direction": ("direction", str),
        "--branch": ("branch_filter", str),
        "--entity": ("entity", str),
    }
    result = {}
    positionals = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--compact":
            result["compact"] = True
        elif arg in flag_spec and i + 1 < len(args):
            key, cast = flag_spec[arg]
            i += 1
            try:
                result[key] = cast(args[i])
            except ValueError:
                print(
                    f"Warning: {arg} requires an integer, "
                    f"got {args[i]!r}",
                    file=sys.stderr,
                )
        elif arg in flag_spec:
            # why: a known flag as the trailing arg has no value — warn
            # instead of silently dropping it.
            print(f"Warning: {arg} requires a value", file=sys.stderr)
        elif not arg.startswith("--"):
            positionals.append(arg)
        i += 1
    if positionals:
        result["query"] = " ".join(positionals)
    return result


# --- Pending sidecar merge ---

def _merge_pending(memory_dir):
    """Hold .graph.lock during merge so hook appends can't race it."""
    import time as _time
    from contextlib import contextmanager
    from pathlib import Path
    from semantic_server.io_utils import merge_pending
    try:
        import fcntl as _fcntl
    except ImportError:
        _fcntl = None
    mem = Path(memory_dir)
    graph = mem / "graph.jsonl"
    pending = graph.with_suffix(".jsonl.pending")

    # why: most CLI invocations have no pending sidecar — skip lock+merge
    # entirely so a busy server can't make every CLI command hang.
    try:
        if not pending.exists() or pending.stat().st_size == 0:
            return
    except OSError:
        return

    @contextmanager
    def _lock():
        # why: non-blocking with a short deadline so a busy server holding
        # the lock can't stall the CLI indefinitely.
        if _fcntl is None:
            yield
            return
        with open(mem / ".graph.lock", "a") as lf:
            deadline = _time.monotonic() + 5.0
            delay = 0.05
            while True:
                try:
                    _fcntl.flock(
                        lf.fileno(),
                        _fcntl.LOCK_EX | _fcntl.LOCK_NB,
                    )
                    break
                except (IOError, OSError):
                    if _time.monotonic() >= deadline:
                        # why: server holds the lock; defer the merge to the
                        # next invocation rather than hang or merge unlocked.
                        raise TimeoutError
                    _time.sleep(delay)
                    delay = min(delay * 2, 0.5)
            try:
                yield
            finally:
                try:
                    _fcntl.flock(lf.fileno(), _fcntl.LOCK_UN)
                except OSError:
                    pass

    try:
        merge_pending(mem, graph, pending, lock=_lock(), invalidate_cb=None)
    except TimeoutError:
        return


# --- Doctor ---

def _load_graph_doctor(graph_path, issues):
    entities = {}
    relations = []
    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    issues.append("Corrupt JSONL line")
                    continue
                if row.get("type") == "entity" and row.get("name"):
                    entities[row["name"]] = row
                elif "from" in row and "to" in row:
                    relations.append(row)
    except OSError as exc:
        issues.append(f"Cannot read graph: {exc}")
    return entities, relations


def _check_stale_decisions(entities, now, issues):
    from datetime import datetime
    for name, ent in entities.items():
        if ent.get("entityType") == "decision":
            for obs in ent.get("observations", []):
                obs_s = obs if isinstance(obs, str) else ""
                if "pending" in obs_s.lower():
                    updated = ent.get("_updated", "")
                    if updated:
                        try:
                            dt = datetime.fromisoformat(
                                updated.replace("Z", "+00:00")
                            )
                            age_days = (now - dt.timestamp()) / 86400
                            if age_days > 30:
                                issues.append(
                                    f"Stale decision: {name} "
                                    f"({int(age_days)}d pending)"
                                )
                        except (ValueError, OSError):
                            pass
                    break


def _check_orphan_relations(entities, relations, issues):
    entity_names = set(entities.keys())
    orphan_count = sum(
        1 for r in relations
        if r.get("from") not in entity_names
        or r.get("to") not in entity_names
    )
    if orphan_count:
        issues.append(
            f"Orphan relations: {orphan_count} "
            f"reference non-existent entities"
        )


def _check_oversized_entities(entities, issues):
    oversized = []
    for name, ent in entities.items():
        n_obs = len(ent.get("observations", []))
        if n_obs > 100:
            oversized.append(f"{name} ({n_obs} obs)")
    if oversized:
        issues.append(
            f"Oversized entities: "
            f"{', '.join(oversized[:5])}"
        )


def _check_vector_layer(issues):
    """Probe venv sidecar, install manifest, and HF model cache."""
    import subprocess
    status = {}
    venv_py_file = os.path.expanduser(
        "~/.claude/easymem/.venv-python"
    )
    manifest_file = os.path.expanduser(
        "~/.claude/easymem/.install-manifest"
    )

    if not os.path.exists(venv_py_file):
        issues.append(
            ".venv-python sidecar missing - run install.sh"
        )
        status["venv"] = "missing"
    else:
        with open(venv_py_file) as f:
            venv_py = f.read().strip()
        if not os.path.exists(venv_py):
            issues.append(f"venv python missing at {venv_py}")
            status["venv"] = "missing"
        else:
            try:
                rc = subprocess.run(
                    [venv_py, "-c", "import model2vec"],
                    capture_output=True, timeout=10,
                ).returncode
            except subprocess.TimeoutExpired:
                rc = -1
                issues.append("model2vec import probe timed out")
            except OSError:
                rc = -1
                issues.append(f"venv python not runnable at {venv_py}")
            if rc != 0:
                issues.append("model2vec import failed in venv")
                status["venv"] = "import_failed"
            else:
                status["venv"] = "ok"

    if not os.path.exists(manifest_file):
        issues.append(
            ".install-manifest missing - install incomplete"
        )
        status["manifest"] = "missing"
    else:
        try:
            with open(manifest_file) as f:
                manifest = json.load(f)
            status["manifest"] = manifest.get("model", "?")
        except (json.JSONDecodeError, OSError):
            issues.append(".install-manifest unreadable")
            status["manifest"] = "unreadable"

    hf_cache_root = os.path.expanduser(
        "~/.cache/huggingface/hub"
    )
    if os.path.exists(hf_cache_root) and any(
        "potion-retrieval-32M" in d
        for d in os.listdir(hf_cache_root)
    ):
        status["model_cache"] = "ok"
    else:
        issues.append(
            "model not in HF cache - first search will download"
        )
        status["model_cache"] = "missing"
    return status


def _load_contradictions(memory_dir, issues):
    """Read contradictions sidecar written by maintenance."""
    empty = {"count": 0, "entities": 0, "samples": []}
    path = os.path.join(memory_dir, "contradictions.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return empty
    if not isinstance(data, dict) or not data:
        return empty
    n_pairs = sum(len(v) for v in data.values() if isinstance(v, list))
    samples = [f"{n} (pairs={len(p)})"
               for n, p in list(data.items())[:3]
               if isinstance(p, list) and p]
    noun = "entity" if len(data) == 1 else "entities"
    issues.append(
        f"Possible contradictions: {n_pairs} pair(s) "
        f"across {len(data)} {noun}"
    )
    return {"count": n_pairs, "entities": len(data), "samples": samples}


def _run_doctor(memory_dir):
    """Health check for the knowledge graph."""
    from collections import Counter
    issues = []
    graph_path = os.path.join(memory_dir, "graph.jsonl")

    if os.path.exists(graph_path):
        entities, relations = _load_graph_doctor(
            graph_path, issues
        )
        now = time.time()
        _check_stale_decisions(entities, now, issues)
        _check_orphan_relations(entities, relations, issues)
        _check_oversized_entities(entities, issues)

        idx_path = os.path.join(memory_dir, "tfidf_index.json")
        if os.path.exists(idx_path):
            idx_age_h = (
                (now - os.path.getmtime(idx_path)) / 3600
            )
            if idx_age_h > 24:
                issues.append(
                    f"Stale index: {idx_age_h:.0f}h old "
                    f"(run: easymem rebuild)"
                )
        elif entities:
            issues.append(
                "No TF-IDF index found (run: easymem rebuild)"
            )

        type_counts = Counter(
            ent.get("entityType", "unknown")
            for ent in entities.values()
        )
    else:
        entities = {}
        relations = []
        type_counts = Counter()
        issues.append("No graph.jsonl found")

    vector = _check_vector_layer(issues)
    contradictions = _load_contradictions(memory_dir, issues)

    status = "healthy" if not issues else "issues_found"
    result = {
        "status": status,
        "graph": {
            "entities": len(entities),
            "relations": len(relations),
            "type_distribution": dict(
                type_counts.most_common(10)
            ),
        },
        "vector_layer": vector,
        "contradictions": contradictions,
        "issues": issues,
        "issue_count": len(issues),
    }

    if _USE_ANSI:
        _print_doctor_ansi(result)
    else:
        print(json.dumps(result, indent=2))


def _print_doctor_ansi(result):
    """ANSI-rendered doctor summary."""
    g = result["graph"]
    vector = result["vector_layer"]
    contradictions = result["contradictions"]
    issues = result["issues"]
    print(f"\n\033[1mEasyMem Doctor\033[0m — {result['status']}")
    print(f"  Graph: {g['entities']}e {g['relations']}r")
    print(
        f"  Vector: venv={vector['venv']} "
        f"model={vector['manifest']} "
        f"cache={vector['model_cache']}"
    )
    if contradictions["count"]:
        noun = ("entity" if contradictions["entities"] == 1
                else "entities")
        print(
            f"  Contradictions: {contradictions['count']} "
            f"pair(s) across {contradictions['entities']} {noun}"
        )
        for s in contradictions["samples"]:
            print(f"    - {s}")
    if issues:
        print(f"\n  Issues ({len(issues)}):")
        for issue in issues:
            print(f"    \033[33m!\033[0m {issue}")
    else:
        print("  \033[32mNo issues found\033[0m")
    print()


# --- Unified tool handlers ---

def _unified_search(a, memory_dir):
    from semantic_server.search import search, search_by_time
    from semantic_server.traverse import traverse_relations

    mode = a.get("mode", "semantic")
    top_k = a.get("top_k", 5)
    if mode == "temporal":
        return search_by_time(
            memory_dir, a.get("since"), a.get("until"),
            top_k,
            branch_filter=a.get("branch_filter"),
            entity_type=a.get("entity_type"),
        )
    elif mode == "graph":
        return traverse_relations(
            a.get("entity", a.get("query", "")), memory_dir,
            a.get("direction", "both"), a.get("max_depth", 2),
        )

    query = a.get("query", "")

    return search(
        query, memory_dir, top_k=top_k,
        branch=a.get("branch"),
        compact=a.get("compact", False),
        max_per_session=a.get("max_per_session"),
    )


def _auto_create_relation_entities(rels, ents, memory_dir,
                                   results):
    existing = (
        {e.get("name", "") for e in ents} if ents else set()
    )
    from semantic_server.graph import load_graph_entities
    existing.update(load_graph_entities(memory_dir).keys())
    auto_ents = []
    for r in rels:
        for key in ("from", "to"):
            name = r.get(key, "")
            if name and name not in existing:
                auto_ents.append({
                    "name": name, "entityType": "unknown",
                    "observations": [
                        "Auto-created from relation reference"
                    ],
                })
                existing.add(name)
    if auto_ents:
        results["auto_created"] = [
            e["name"] for e in auto_ents
        ]
        return ents + auto_ents
    return ents


def _handle_obs_map(obs_map, memory_dir, results):
    from semantic_server.tools import add_observations
    obs_results = {}
    for entity, obs_list in obs_map.items():
        if isinstance(obs_list, list):
            obs_results[entity] = add_observations(
                entity, obs_list, memory_dir
            )
    results["observations"] = obs_results


def _unified_write(a, memory_dir):
    from semantic_server.tools import (
        create_entities, create_relations, add_observations,
    )
    results = {}
    ents = a.get("entities", [])
    rels = a.get("relations", [])
    obs_map = a.get("observations", {})

    if rels:
        ents = _auto_create_relation_entities(
            rels, ents, memory_dir, results
        )

    if ents:
        results["entities"] = create_entities(ents, memory_dir)
    if rels:
        results["relations"] = create_relations(
            rels, memory_dir
        )
    if obs_map and isinstance(obs_map, dict):
        _handle_obs_map(obs_map, memory_dir, results)

    if not ents and not rels and not obs_map:
        entity_name = a.get("entity", "")
        observation = a.get("observation", "")
        if entity_name and observation:
            results = add_observations(
                entity_name, [observation], memory_dir
            )
    return results or {"error": "Nothing to write"}


def _unified_recall(a, memory_dir):
    query = a.get("query", "")
    if not query:
        return {"error": "query required"}
    from semantic_server.search import search
    from semantic_server.traverse import traverse_relations

    sr = search(
        query, memory_dir, top_k=a.get("top_k", 3),
        branch=a.get("branch"), compact=True,
    )
    results = sr.get("results", [])
    if not results:
        return sr
    enriched = []
    for r in results[:3]:
        entity = r.get("entity", "")
        tr = traverse_relations(entity, memory_dir, "both", 1)
        connected = [
            {
                "name": n.get("name", ""),
                "type": n.get("entityType", ""),
                "relation": n.get("_relation", ""),
            }
            for n in tr.get("nodes", [])
            if n.get("name") != entity
        ][:5]
        enriched.append({
            "entity": entity,
            "score": r.get("score", 0),
            "entityType": r.get("entityType", ""),
            "connected": connected,
        })
    return {
        "results": enriched,
        "total_indexed": sr.get("total_indexed", 0),
    }


def _unified_decide(a, memory_dir):
    from semantic_server.tools import (
        create_decision, update_decision_outcome,
    )
    if a.get("action", "create") == "resolve":
        return update_decision_outcome(a, memory_dir)
    return create_decision(a, memory_dir)


def _unified_remove(a, memory_dir):
    from semantic_server.tools import (
        rename_entity, remove_observations, delete_entities,
    )
    action = a.get("action", "")
    if action == "rename":
        return rename_entity(
            a.get("old_name", ""), a.get("new_name", ""),
            memory_dir,
        )
    if action == "remove_observations":
        return remove_observations(
            a.get("entity", ""), a.get("observations", []),
            memory_dir,
        )
    # Require explicit "delete" action — silent removal is too dangerous.
    if action != "delete":
        return {"error": "missing action"}
    names = (
        a.get("entity_names", [])
        or ([a.get("entity")] if a.get("entity") else [])
    )
    return delete_entities(names, memory_dir)


def _unified_status(a, memory_dir):
    from semantic_server.tools import graph_stats, list_decisions
    stats = graph_stats(memory_dir)
    pending = list_decisions(
        memory_dir, stale_days=2
    ).get("decisions", [])
    if pending:
        stats["decision_nudge"] = {
            "pending_count": len(pending),
            "message": (
                f"{len(pending)} decisions pending > 2 days"
            ),
            "oldest": [
                d.get("title", "") for d in pending[:5]
            ],
        }
    return stats


# --- Aliases ---

def _aliases_path(memory_dir):
    return os.path.join(memory_dir, "aliases.json")


def _read_aliases(memory_dir):
    """Return list of groups; tolerate missing/corrupt files."""
    try:
        with open(_aliases_path(memory_dir), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    groups = data.get("groups", [])
    return groups if isinstance(groups, list) else []


def _write_aliases(memory_dir, groups):
    os.makedirs(memory_dir, exist_ok=True)
    path = _aliases_path(memory_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"groups": groups}, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def _aliases_lock(memory_dir):
    # why: serialize alias read-modify-write so concurrent add/remove don't
    # lose updates (the last os.replace would otherwise win).
    try:
        import fcntl as _fcntl
    except ImportError:
        _fcntl = None
    os.makedirs(memory_dir, exist_ok=True)
    if _fcntl is None:
        yield
        return
    with open(os.path.join(memory_dir, ".aliases.lock"), "a") as lf:
        _fcntl.flock(lf.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                _fcntl.flock(lf.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass


def _cmd_aliases_add(memory_dir, words):
    if len(words) < 2:
        return {"error": "need canonical + at least one synonym"}
    new_group = [w.lower().strip() for w in words if w.strip()]
    canonical = new_group[0]
    with _aliases_lock(memory_dir):
        groups = _read_aliases(memory_dir)
        # Replace any existing group with same canonical
        groups = [g for g in groups
                  if not (isinstance(g, list) and g and
                          str(g[0]).lower().strip() == canonical)]
        groups.append(new_group)
        _write_aliases(memory_dir, groups)
    return {"added": new_group, "total_groups": len(groups)}


def _cmd_aliases_list(memory_dir):
    return {"groups": _read_aliases(memory_dir)}


def _cmd_aliases_remove(memory_dir, canonical):
    target = canonical.lower().strip()
    with _aliases_lock(memory_dir):
        groups = _read_aliases(memory_dir)
        kept = [g for g in groups
                if not (isinstance(g, list) and g and
                        str(g[0]).lower().strip() == target)]
        _write_aliases(memory_dir, kept)
    return {"removed": target, "remaining": len(kept)}


def _run_aliases(memory_dir, extra_args):
    if not extra_args:
        return {"error": "usage: aliases <add|list|remove> [args]"}
    op = extra_args[0]
    rest = extra_args[1:]
    if op == "add":
        return _cmd_aliases_add(memory_dir, rest)
    if op == "list":
        return _cmd_aliases_list(memory_dir)
    if op == "remove":
        if not rest:
            return {"error": "remove requires <canonical>"}
        return _cmd_aliases_remove(memory_dir, rest[0])
    return {"error": f"unknown aliases op '{op}'"}


def _run_slots(memory_dir, extra_args):
    from semantic_server.slots import (
        SLOT_KEYS, get_slot, set_slot, list_slots,
    )
    if not extra_args:
        return {"error": "usage: slots <get|set|list> [args]"}
    op = extra_args[0]
    rest = extra_args[1:]
    if op == "list":
        return {"slots": list_slots(memory_dir)}
    if op == "get" and rest:
        return {"key": rest[0], "value": get_slot(memory_dir, rest[0])}
    if op == "set" and len(rest) >= 2:
        try:
            set_slot(memory_dir, rest[0], " ".join(rest[1:]))
        except ValueError as exc:
            return {"error": str(exc), "valid_keys": list(SLOT_KEYS)}
        return {"ok": True, "key": rest[0]}
    return {"error": f"unknown slots op '{op}' or missing args"}


# --- Nudge suppression ---

def _cmd_nudge(extra_args):
    if not extra_args or extra_args[0] != "suppress":
        print(json.dumps({"error": "usage: nudge suppress"}, indent=2))
        return
    import hashlib
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    project = os.path.realpath(project)
    h = hashlib.md5(project.encode("utf-8")).hexdigest()
    state_dir = os.path.join(
        os.environ.get("XDG_CONFIG_HOME")
        or os.path.join(os.path.expanduser("~"), ".config"),
        "easymem", "nudge",
    )
    os.makedirs(state_dir, exist_ok=True)
    marker = os.path.join(state_dir, f"{h}.suppress")
    with open(marker, "w"):
        pass
    print(json.dumps(
        {"ok": True, "suppressed": project, "marker": marker},
        indent=2,
    ))


# --- Code index ---

def _cmd_index_code(args, memory_dir):
    from semantic_server.code_index import index_project
    project_root = args[0] if args else os.getcwd()
    result = index_project(memory_dir, project_root)
    if result.get("error"):
        print(f"index-code failed: {result['error']}", file=sys.stderr)
        return 1
    print(
        f"indexed: {result['indexed']} files, "
        f"{result['symbols']} symbols, "
        f"removed: {result['removed']} stale, "
        f"relations: {result['relations']}"
    )
    return 0


# --- Diff ---

def _run_diff(memory_dir):
    """Show entities changed since last session."""
    marker = os.path.join(memory_dir, ".last-session-start")
    try:
        with open(marker) as f:
            last_ts = f.read().strip()
    except OSError:
        last_ts = None

    if not last_ts:
        em = os.path.expanduser("~/.claude/easymem/easymem")
        print("No previous session recorded. "
              f"Run `{em} status` in a new session first.")
        return

    graph_path = os.path.join(memory_dir, "graph.jsonl")
    new_entities = []
    updated_entities = []
    new_decisions = []
    resolved_decisions = []

    try:
        with open(graph_path, encoding="utf-8") as f:
            entities = {}
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") != "entity":
                        continue
                    name = obj.get("name", "")
                    if not name:
                        continue
                    if name in entities:
                        prev = entities[name]
                        new_u = obj.get("_updated", "")
                        if new_u and (not prev.get("_updated")
                                      or new_u > prev["_updated"]):
                            # why: keep the latest revision's full state so
                            # resolved outcomes / type changes aren't masked.
                            entities[name] = dict(obj)
                    else:
                        entities[name] = dict(obj)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        print("Cannot read graph.")
        return

    for name, info in entities.items():
        etype = info.get("entityType", "")
        created = info.get("_created", "")
        updated = info.get("_updated", "")
        if created and created > last_ts:
            entry = {"name": name, "type": etype,
                     "timestamp": created}
            if etype == "decision":
                new_decisions.append(entry)
            else:
                new_entities.append(entry)
        elif updated and updated > last_ts:
            if etype == "decision":
                obs = info.get("observations", [])
                resolved = any(
                    isinstance(o, str)
                    and o.startswith("Outcome: ")
                    and not o.startswith("Outcome: pending")
                    for o in obs
                )
                if resolved:
                    resolved_decisions.append(
                        {"name": name, "timestamp": updated}
                    )
                    continue
            updated_entities.append(
                {"name": name, "type": etype,
                 "timestamp": updated}
            )

    if _USE_ANSI:
        total = (len(new_entities) + len(updated_entities)
                 + len(new_decisions) + len(resolved_decisions))
        if total == 0:
            print(f"No changes since {last_ts[:16]}.")
            return
        print(f"\n\033[1mChanges since {last_ts[:16]}\033[0m\n")
        if new_entities:
            print(f"  \033[32m+ New ({len(new_entities)}):\033[0m")
            for e in new_entities[:10]:
                tag = f" ({e['type']})" if e['type'] else ""
                print(f"    {e['name']}{tag}")
            if len(new_entities) > 10:
                print(f"    +{len(new_entities) - 10} more")
        if updated_entities:
            print(
                f"  \033[33m~ Updated "
                f"({len(updated_entities)}):\033[0m"
            )
            for e in updated_entities[:10]:
                tag = f" ({e['type']})" if e['type'] else ""
                print(f"    {e['name']}{tag}")
            if len(updated_entities) > 10:
                print(
                    f"    +{len(updated_entities) - 10} more"
                )
        if new_decisions:
            print(
                f"  \033[35mDecisions made "
                f"({len(new_decisions)}):\033[0m"
            )
            for d in new_decisions[:5]:
                name = d["name"]
                if name.lower().startswith("decision: "):
                    name = name[10:]
                print(f"    {name}")
        if resolved_decisions:
            print(
                f"  \033[36mDecisions resolved "
                f"({len(resolved_decisions)}):\033[0m"
            )
            for d in resolved_decisions[:5]:
                name = d["name"]
                if name.lower().startswith("decision: "):
                    name = name[10:]
                print(f"    {name}")
        print()
    else:
        print(json.dumps({
            "since": last_ts,
            "new": new_entities,
            "updated": updated_entities,
            "new_decisions": new_decisions,
            "resolved_decisions": resolved_decisions,
        }, indent=2))


# --- TTY formatting ---

def _format_tty_output(tool_name, result, top_k=5):
    """Format result for human-readable TTY output."""
    if tool_name in ("search", "recall"):
        res_list = result.get("results", [])[:top_k]
        print(
            f"Search "
            f"({result.get('total_indexed', 0)} indexed):"
        )
        for r in res_list:
            name = r.get("entity", "")
            etype = r.get("entityType", "")
            score = r.get("score", 0.0)
            print(
                f"\n- \033[1;36m{name}\033[0m "
                f"({etype}) [score: {score:.2f}]"
            )
            if "observations" in r:
                obs = r["observations"]
                for o in obs[:3]:
                    print(f"    \u2022 {o}")
                if len(obs) > 3:
                    print(f"    \u2022 \u2026 +{len(obs) - 3} more")
            if "connected" in r:
                conns = [
                    f"{c.get('relation', '--')}->"
                    f"{c.get('name', '')}"
                    for c in r["connected"]
                ]
                if conns:
                    print(
                        f"    \u21b3 {', '.join(conns)}"
                    )
    elif tool_name == "status":
        print("\n\033[1mMemory Diagnostics\033[0m")
        for k, v in result.items():
            if (isinstance(v, dict)
                    and k == "decision_nudge"):
                print(
                    f"\n  \033[1;33m\u26a0\ufe0f  "
                    f"{v.get('message', '')}\033[0m"
                )
                for old_d in v.get("oldest", []):
                    print(f"      - {old_d}")
            elif isinstance(v, dict):
                print(f"\n  {k}:")
                for subk, subv in v.items():
                    print(f"    {subk}: {subv}")
            else:
                print(f"  {k}: {v}")
        print("")
    else:
        print(json.dumps(result, indent=2))


# --- Arg parsing ---

def _parse_tool_args(tool_name, extra_args):
    """Parse CLI arguments into a tool_args dict."""
    _POSITIONAL_TOOLS = {
        "search", "recall", "status",
        "doctor", "rebuild", "diff",
    }
    if tool_name in _POSITIONAL_TOOLS:
        return _parse_positional(extra_args)
    if extra_args:
        first = extra_args[0]
        if first.startswith('{') or first.startswith('['):
            try:
                tool_args = json.loads(first)
            except (json.JSONDecodeError, ValueError) as e:
                print(
                    f"Error: invalid JSON: {e}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not isinstance(tool_args, dict):
                print(
                    "Error: args must be a JSON object",
                    file=sys.stderr,
                )
                sys.exit(1)
            return tool_args
        return _parse_positional(extra_args)
    return {}


def main():
    memory_dir, args = _resolve_memory_dir(sys.argv[1:])

    if not args:
        _usage()

    tool_name = args[0]
    extra_args = args[1:]

    tool_args = _parse_tool_args(tool_name, extra_args)

    # why: nudge runs BEFORE bootstrap — its whole point is to opt out of
    # auto-creating .easymem/ in the project.
    if tool_name == "nudge":
        _cmd_nudge(extra_args)
        return

    if tool_name in ("help", "--help", "-h"):
        _usage(sys.stdout, 0)
        return

    if tool_name in ("export", "import"):
        import subprocess
        project_dir = os.path.dirname(os.path.abspath(memory_dir)) \
            or os.getcwd()
        # why: .sh scripts honor EASYMEM_DIR if set; propagate the
        # resolved path so --easymem-dir overrides reach them.
        env = {**os.environ, "EASYMEM_DIR": memory_dir}
        if tool_name == "export":
            sh = os.path.join(_script_dir, "export-easymem.sh")
            cmd = [sh, project_dir, *extra_args]
        else:
            if not extra_args:
                print(
                    "Usage: easymem import <bundle_file>",
                    file=sys.stderr,
                )
                sys.exit(1)
            sh = os.path.join(_script_dir, "import-easymem.sh")
            cmd = [sh, extra_args[0], project_dir]
        if not os.access(sh, os.X_OK):
            print(f"Error: {sh} not found or not executable",
                  file=sys.stderr)
            sys.exit(1)
        sys.exit(subprocess.run(cmd, env=env).returncode)

    if not (tool_name in _READ_ONLY_CMDS and os.path.isdir(memory_dir)):
        was_missing = not os.path.isdir(memory_dir)
        from semantic_server.bootstrap import bootstrap
        if not bootstrap(memory_dir, load_index_on_start=False):
            print(
                f"Error: Could not initialize EASYMEM_DIR {memory_dir}",
                file=sys.stderr,
            )
            sys.exit(1)
        if was_missing and _USE_ANSI:
            print(
                f"Initialized knowledge graph at {memory_dir}",
                file=sys.stderr,
            )

    # Merge pending sidecar before any reads
    _merge_pending(memory_dir)

    # --- Commands that don't need semantic_server ---
    if tool_name == "rebuild":
        import maintenance
        # Mark dirty and defer; --rebuild-now forces immediate.
        if "--rebuild-now" in extra_args:
            indexed = maintenance.rebuild_index(memory_dir)
            print(json.dumps({
                "rebuilt": indexed > 0,
                "indexed": indexed,
            }))
        else:
            dirty = os.path.join(memory_dir, ".index-dirty")
            try:
                with open(dirty, "a"):
                    pass
            except OSError:
                pass
            print(json.dumps({"queued": True, "hint": "index will rebuild at next maintenance run; use --rebuild-now for immediate"}))
        return

    if tool_name == "doctor":
        _run_doctor(memory_dir)
        return

    if tool_name == "diff":
        _run_diff(memory_dir)
        return

    if tool_name == "aliases":
        result = _run_aliases(memory_dir, extra_args)
        print(json.dumps(result, indent=2))
        return

    if tool_name == "slots":
        result = _run_slots(memory_dir, extra_args)
        print(json.dumps(result, indent=2))
        return

    if tool_name == "index-code":
        _cmd_index_code(extra_args, memory_dir)
        return

    # --- Commands that need semantic_server ---
    from semantic_server.graph import load_index
    from semantic_server.recall import flush_recall_counts

    try:
        load_index(memory_dir)
    except Exception as exc:
        print(
            f"Warning: index load failed ({exc}), "
            f"search may be degraded",
            file=sys.stderr,
        )

    dispatch = {
        "search": lambda a: _unified_search(a, memory_dir),
        "write": lambda a: _unified_write(a, memory_dir),
        "recall": lambda a: _unified_recall(a, memory_dir),
        "decide": lambda a: _unified_decide(a, memory_dir),
        "remove": lambda a: _unified_remove(a, memory_dir),
        "status": lambda a: _unified_status(a, memory_dir),
    }

    handler = dispatch.get(tool_name)
    if handler is None:
        print(
            f"Error: unknown command '{tool_name}'",
            file=sys.stderr,
        )
        _usage()

    try:
        result = handler(tool_args)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        print(
            json.dumps({"error": str(exc)}, indent=2)
        )
        sys.exit(1)

    top_k = tool_args.get("top_k", 5)

    # TTY human-readable formatting or raw JSON
    if (_USE_ANSI and isinstance(result, dict)
            and not result.get("error")):
        _format_tty_output(tool_name, result, top_k=top_k)
    else:
        print(json.dumps(result, indent=2))

    # Mark dirty after write ops; rebuild deferred to next maintenance.
    if tool_name in ("write", "decide", "remove"):
        dirty = os.path.join(memory_dir, ".index-dirty")
        try:
            with open(dirty, "a"):
                pass
        except OSError:
            pass

    # Flush recall counts
    try:
        flush_recall_counts()
    except Exception:
        pass


if __name__ == "__main__":
    main()
