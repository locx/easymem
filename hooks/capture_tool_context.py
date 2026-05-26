#!/usr/bin/env python3
"""PostToolUse hook: surface file warnings from the knowledge graph.

Usage: python3 capture_tool_context.py <input_json> <graph_path>
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time as _time_mod

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

_WARN_SCAN_LINE_BUDGET = 5_000

# Persisted error/stderr from tool responses can carry credentials; scrub
# before it lands in graph.jsonl where future searches surface it verbatim.
_SECRET_RE = re.compile(
    r"AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[0-9A-Za-z]{20,}"
    r"|sk-[0-9A-Za-z_\-]{20,}"
    r"|xox[abpros]-[0-9A-Za-z\-]{10,}"
    r"|Bearer\s+[A-Za-z0-9._~+/=\-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def _scrub(s: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", s) if s else s


def _sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _iso_now() -> str:
    return _time_mod.strftime("%Y-%m-%dT%H:%M:%SZ", _time_mod.gmtime())


def _current_branch(cwd: str = "") -> str:
    head_path = os.path.join(cwd or os.getcwd(), ".git", "HEAD")
    try:
        with open(head_path) as f:
            content = f.read().strip()
        if content.startswith("ref: refs/heads/"):
            return content[16:]
    except OSError:
        pass
    return ""


def _append_episode(graph_path: str, name: str,
                    observations: list,
                    source: str = "") -> None:
    """Append episode under the same .graph.lock maintenance.run holds.

    Without this lock, an append racing maintenance's read+rewrite is
    silently dropped on os.replace.
    """
    entry = {
        "type": "entity",
        "name": name,
        "entityType": "episode",
        "observations": observations,
        "_branch": _current_branch(),
        "_created": _iso_now(),
        "_updated": _iso_now(),
    }
    if source:
        entry["_source"] = source
    line = json.dumps(entry, separators=(",", ":")) + "\n"
    lock_path = os.path.join(
        os.path.dirname(graph_path) or ".", ".graph.lock"
    )
    lock_fd = None
    if _fcntl is not None:
        try:
            lock_fd = open(lock_path, "a")
            _fcntl.flock(lock_fd.fileno(), _fcntl.LOCK_EX)
        except OSError:
            if lock_fd is not None:
                lock_fd.close()
                lock_fd = None
    if _fcntl is not None and lock_fd is None:
        # why: unlocked append races maintenance's read-rewrite-replace
        # and loses the write; defer via .pending sidecar instead.
        pending_path = graph_path + ".pending"
        try:
            with open(pending_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        except OSError:
            pass
        return
    try:
        with open(graph_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    finally:
        if lock_fd is not None:
            try:
                _fcntl.flock(lock_fd.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass
            lock_fd.close()


def mint_error(input_path: str, graph_path: str) -> None:
    try:
        with open(input_path) as f:
            data = json.load(f)
    except Exception:
        return
    err = (data.get("tool_response", {}).get("error")
           or data.get("tool_response", {}).get("stderr", "")).strip()
    if not err:
        return
    tool = data.get("tool_name", "")
    target = (data.get("tool_input", {}).get("file_path")
              or data.get("tool_input", {}).get("command", ""))[:200]
    target = _scrub(target)
    err = _scrub(err)
    stable_key = f"{target}|{err[:200]}"
    name = f"episode:err:{_sha8(stable_key)}"
    obs = [
        f"[ERROR] tool={tool}",
        f"target={target}",
        f"msg={err[:300]}",
    ]
    _append_episode(
        graph_path, name, obs,
        source="hook:capture-tool-context:error",
    )


CHURN_THRESHOLD = 3
CHURN_WINDOW_S = 300


def mint_churn(input_path: str, graph_path: str) -> None:
    try:
        with open(input_path) as f:
            data = json.load(f)
    except Exception:
        return
    tool = data.get("tool_name", "")
    if tool not in ("Edit", "Write"):
        return
    fp = data.get("tool_input", {}).get("file_path", "")
    if not fp:
        return
    sid = os.environ.get("CLAUDE_SESSION_ID", "unknown")
    safe_sid = "".join(
        c if c.isalnum() or c in "_-" else "_" for c in sid
    )[:64]
    path_hash = _sha8(fp)
    marker = f"/tmp/.claude-easymem-churn-{safe_sid}-{path_hash}"
    sentinel = marker + ".minted"
    # Re-arm after the debounce window: stale sentinel (and its companion
    # marker) would otherwise block all future mints for this (sid, file).
    try:
        sent_age = _time_mod.time() - os.path.getmtime(sentinel)
        if sent_age >= CHURN_WINDOW_S:
            try:
                os.unlink(sentinel)
            except OSError:
                pass
            try:
                os.unlink(marker)
            except OSError:
                pass
    except OSError:
        pass
    try:
        with open(marker, "ab") as f:
            f.write(b".")
    except OSError:
        return
    try:
        size = os.path.getsize(marker)
    except OSError:
        return
    if size < CHURN_THRESHOLD:
        return
    try:
        fd = os.open(
            sentinel, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
        os.close(fd)
    except FileExistsError:
        return
    name = f"episode:churn:{_sha8(fp + sid)}"
    obs = [
        f"[CHURN] {CHURN_THRESHOLD}+ edits in "
        f"<{CHURN_WINDOW_S // 60}min",
        f"file={fp}",
    ]
    _append_episode(
        graph_path, name, obs,
        source="hook:capture-tool-context:churn",
    )


def mint_commit(graph_path: str, sha: str, msg: str) -> None:
    sha = (sha or "").strip()
    if not sha:
        return
    name = f"episode:commit:{sha[:8]}"
    safe_msg = _scrub((msg or "")[:200])
    obs = [
        f"[COMMIT] sha={sha}",
        f"msg={safe_msg}",
    ]
    _append_episode(
        graph_path, name, obs,
        source="hook:capture-tool-context:commit",
    )


def _check_file_warnings(graph_path, filename, session_id):
    """Check graph for warnings/decisions about a file."""
    if not filename or filename == '?':
        return ""

    safe_sid = "".join(
        c if c.isalnum() or c in ('_', '-')
        else '_' for c in session_id
    )[:64]
    # Key marker by sha256 of full absolute path to avoid basename collisions
    abs_path = os.path.abspath(filename)
    path_hash = hashlib.sha256(abs_path.encode()).hexdigest()[:16]
    marker = (
        f"/tmp/.claude-easymem-warned-{safe_sid}-{path_hash}"
    )
    try:
        marker_age = _time_mod.time() - os.path.getmtime(marker)
        if 0 <= marker_age < 86400:  # suppress for 24h only
            return ""
        os.unlink(marker)  # expired — re-surface warning
    except OSError:
        pass  # marker doesn't exist — proceed

    basename = os.path.basename(filename)
    match_names = {basename, filename, abs_path}

    warnings = []
    decisions = []
    relations_out = []
    line_count = 0

    try:
        with open(
            graph_path, encoding="utf-8",
            errors="replace",
        ) as f:
            for line in f:
                line_count += 1
                if line_count > _WARN_SCAN_LINE_BUDGET:
                    print(
                        f"[capture_tool_context] scan capped at "
                        f"{_WARN_SCAN_LINE_BUDGET} lines for {basename}",
                        file=sys.stderr,
                    )
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    t = obj.get("type")
                    if t == "entity":
                        name = obj.get("name", "")
                        etype = obj.get("entityType", "")
                        obs = obj.get("observations", [])
                        if name in match_names:
                            for o in obs:
                                if not isinstance(o, str):
                                    continue
                                if (etype == "file-warning"
                                        or "[WARNING]" in o):
                                    warnings.append(o)
                        elif etype == "decision":
                            for o in obs:
                                if (isinstance(o, str)
                                        and basename in o):
                                    short = name
                                    if short.startswith(
                                        "decision: "
                                    ):
                                        short = short[10:]
                                    decisions.append(short)
                                    break
                    elif t == "relation":
                        fr = obj.get("from", "")
                        to = obj.get("to", "")
                        rt = obj.get("relationType", "")
                        if fr in match_names:
                            relations_out.append(
                                f"{rt} -> {to}"
                            )
                        elif to in match_names:
                            relations_out.append(
                                f"{fr} -{rt}-> {basename}"
                            )
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return ""

    if not warnings and not decisions \
            and not relations_out:
        return ""

    try:
        with open(marker, 'w') as f:
            f.write('1')
    except OSError:
        pass

    parts = []
    for items, header, limit in (
        (warnings, f"Warnings for {basename}:", 5),
        (decisions, "Related decisions:", 3),
        (relations_out, "Relations:", 5),
    ):
        if items:
            parts.append(header)
            for item in items[:limit]:
                parts.append(f"  - {item[:200]}")

    return "\n".join(parts)


def main():
    if len(sys.argv) < 3:
        sys.exit(2)
    input_path = sys.argv[1]
    graph_path = sys.argv[2]

    try:
        with open(input_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        sys.exit(1)

    tool = data.get('tool_name', '')
    if tool not in ('Edit', 'Write'):
        sys.exit(0)

    file_path = data.get('tool_input', {}).get(
        'file_path', '?'
    )
    session_id = os.environ.get(
        'CLAUDE_SESSION_ID', 'unknown'
    )
    warning_text = _check_file_warnings(
        graph_path, file_path, session_id
    )
    if warning_text:
        # Emit via hookSpecificOutput JSON so PostToolUse output is not swallowed
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": warning_text,
            }
        }
        print(json.dumps(payload))


if __name__ == '__main__':
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--mint-error", nargs=2, metavar=("INPUT", "GRAPH"))
    p.add_argument("--mint-churn", nargs=2, metavar=("INPUT", "GRAPH"))
    p.add_argument("--mint-commit", nargs=3,
                   metavar=("GRAPH", "SHA", "MSG"))
    p.add_argument("rest", nargs="*")
    args, _unknown = p.parse_known_args()

    if args.mint_error:
        mint_error(*args.mint_error)
        sys.exit(0)
    if args.mint_churn:
        mint_churn(*args.mint_churn)
        sys.exit(0)
    if args.mint_commit:
        mint_commit(*args.mint_commit)
        sys.exit(0)
    main()
