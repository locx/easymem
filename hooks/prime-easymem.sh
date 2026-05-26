#!/bin/bash
# SessionStart hook: run maintenance + smart EasyMem recall.
# Global — works for any project with .easymem/ dir.
# Runs in both CLI and VSCode (maintenance is always useful).

[ -n "${CLAUDE_PROJECT_DIR:-}" ] || exit 0

EM_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/easymem}"
EASYMEM_DIR="${CLAUDE_PROJECT_DIR}/.easymem"

# Skip if project has no EasyMem setup
[ -d "${EASYMEM_DIR}" ] || exit 0

# Run maintenance regardless of environment (throttled internally to 1x/day)
if [ -f "$EM_ROOT/maintenance.py" ]; then
    ERR_LOG="${EASYMEM_DIR}/maintenance.err"
    # Rotate error log if >100KB
    if [ -f "$ERR_LOG" ]; then
        ERR_SIZE=$(wc -c < "$ERR_LOG" 2>/dev/null | tr -d ' ') || ERR_SIZE=0
        if [ "${ERR_SIZE:-0}" -gt 100000 ] 2>/dev/null; then
            mv -f "$ERR_LOG" "${ERR_LOG}.old" 2>/dev/null || true
        fi
    fi
    # why: fully backgrounded with a Python-side SIGALRM cap so macOS (no
    # coreutils `timeout`) doesn't end up with unbounded background runs.
    python3 "$EM_ROOT/maintenance.py" --timeout 60 \
        "${CLAUDE_PROJECT_DIR}" 2>>"$ERR_LOG" &
    disown 2>/dev/null || true
fi

# Smart recall — scored entities, compact, with relations + stats
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
if [ -f "${SCRIPT_DIR}/smart_recall.py" ]; then
    RECALL_OUT=$(python3 "${SCRIPT_DIR}/smart_recall.py" "${EASYMEM_DIR}" 2>/dev/null)
    RECALL_EXIT=$?
    if [ $RECALL_EXIT -ne 0 ] || [ -z "$RECALL_OUT" ]; then
        echo "EasyMem: use \`easymem search <query>\` or \`easymem recall <query>\` for details."
    else
        echo "$RECALL_OUT"
    fi
else
    echo "EasyMem: use \`easymem search <query>\` or \`easymem recall <query>\` for details."
fi

exit 0
