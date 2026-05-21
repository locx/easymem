#!/bin/bash
# PostToolUse hook: capture observations from relevant tool calls.
# Passes stdin via temp file to Python (no shell interpolation).
# Append-only writes (no full graph rewrite).
# Caps stdin at 5MB — bumped from 50KB after truncating the JSON
# envelope silently broke mint_error on large stderr / stack traces.
# Throttled: skips if last capture was <30s ago.

[ -n "${CLAUDE_PROJECT_DIR:-}" ] || exit 0

[ -d "${CLAUDE_PROJECT_DIR}/.easymem" ] || exit 0
[ -n "${CLAUDE_SESSION_ID:-}" ] || exit 0

EASYMEM_PY="$(cat "${HOME}/.claude/easymem/.venv-python" 2>/dev/null || echo python3)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1

EASYMEM_DIR="${CLAUDE_PROJECT_DIR}/.easymem"
GRAPH="${EASYMEM_DIR}/graph.jsonl"
# Create graph if missing — resilient bootstrapping
[ -f "$GRAPH" ] || touch "$GRAPH"

SAFE_SID="${CLAUDE_SESSION_ID//[^a-zA-Z0-9_-]/_}"

# Portable stat helper: get file mtime as epoch seconds
_file_mtime() {
    date -r "$1" +%s 2>/dev/null \
        || stat -c%Y "$1" 2>/dev/null \
        || python3 -c "import os,sys; print(int(os.path.getmtime(sys.argv[1])))" "$1" 2>/dev/null \
        || echo 0
}

# Clean up stale toolcap temp files older than 1 hour
find /tmp -maxdepth 1 -name '.claude-easymem-toolcap-*' -mmin +60 -delete 2>/dev/null || true

# Save stdin to temp file (capped at 5MB), pass path to Python
TMPINPUT=$(mktemp /tmp/.claude-easymem-toolcap-XXXXXX) || exit 1
chmod 600 "$TMPINPUT"
trap 'rm -f "$TMPINPUT" 2>/dev/null' EXIT
head -c 5242880 > "$TMPINPUT"

# Mint phase — always runs (idempotent, deterministic name)
"${EASYMEM_PY}" "${SCRIPT_DIR}/capture_tool_context.py" \
    --mint-error "$TMPINPUT" "$GRAPH" 2>/dev/null || true
"${EASYMEM_PY}" "${SCRIPT_DIR}/capture_tool_context.py" \
    --mint-churn "$TMPINPUT" "$GRAPH" 2>/dev/null || true

# Throttle: skip if last capture was <30s ago
MARKER="/tmp/.claude-easymem-toolcap-${SAFE_SID}"
if [ -f "$MARKER" ]; then
    NOW=$(date +%s)
    LAST=$(_file_mtime "$MARKER")
    ELAPSED=$(( NOW - ${LAST:-0} ))
    if [ "$ELAPSED" -lt 30 ]; then
        exit 0
    fi
fi

# Use standalone .py for bytecode caching (.pyc)
python3 "${SCRIPT_DIR}/capture_tool_context.py" "$TMPINPUT" "$GRAPH"
PY_EXIT=$?

# Update throttle marker after successful run
if [ "$PY_EXIT" -eq 0 ]; then
    touch "$MARKER"
fi

exit "$PY_EXIT"
