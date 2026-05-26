#!/bin/bash
# PreCompact hook: inject top entities + pending decisions before
# Claude Code compresses context. Compaction is the moment recall
# survival matters most.

[ -n "${CLAUDE_PROJECT_DIR:-}" ] || exit 0

EASYMEM_DIR="${CLAUDE_PROJECT_DIR}/.easymem"
[ -d "${EASYMEM_DIR}" ] || exit 0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 0
[ -f "${SCRIPT_DIR}/smart_recall.py" ] || exit 0

# smart_recall has its own internal time budget; an extra `timeout`
# wrapper would silently fail when coreutils isn't installed (macOS).
RECALL_OUT=$(python3 "${SCRIPT_DIR}/smart_recall.py" \
    "${EASYMEM_DIR}" --compact 2>/dev/null)

[ -z "$RECALL_OUT" ] && exit 0

printf '%s\n%s\n' 'Memory snapshot (pre-compact):' "$RECALL_OUT"
