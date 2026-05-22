#!/bin/bash
# SessionStart hook: auto-init project on first encounter; nudge thereafter.
# Opt out with `easymem nudge suppress`.

EM_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/easymem}"

# --- Guards ---
[ -f "$EM_ROOT/maintenance.py" ] || exit 0
[ -n "${CLAUDE_PROJECT_DIR:-}" ] || exit 0
[ -d "${CLAUDE_PROJECT_DIR}/.easymem" ] && exit 0

# --- Project hash (realpath matches the CLI's os.path.realpath) ---
_project_hash() {
    python3 - "$1" 2>/dev/null <<'PY'
import hashlib, os, sys
print(hashlib.md5(os.path.realpath(sys.argv[1]).encode()).hexdigest())
PY
}

PROJECT_HASH=$(_project_hash "$CLAUDE_PROJECT_DIR")
# why: bail if python3 is unavailable — easymem is Python-based so a missing
# python3 means the runtime can't function anyway.
[ -n "$PROJECT_HASH" ] || exit 0

# --- State paths ---
STATE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/easymem/nudge"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
SUPPRESS_MARKER="$STATE_DIR/$PROJECT_HASH.suppress"
COUNT_FILE="$STATE_DIR/$PROJECT_HASH.count"
THROTTLE="/tmp/.claude-easymem-nudge-$PROJECT_HASH"

# --- Suppress always wins ---
[ -f "$SUPPRESS_MARKER" ] && exit 0

# --- 24h throttle ---
if [ -f "$THROTTLE" ]; then
    LAST=$(date -r "$THROTTLE" +%s 2>/dev/null \
        || stat -c%Y "$THROTTLE" 2>/dev/null \
        || echo 0)
    [ "$(( $(date +%s) - LAST ))" -lt 86400 ] && exit 0
fi
touch "$THROTTLE"

# --- Locate setup-project.sh (source-dir override → runtime fallback) ---
_locate_setup() {
    local src=""
    if [ -f "$EM_ROOT/.source-dir" ]; then
        src="$(cat "$EM_ROOT/.source-dir")/setup-project.sh"
        if [ -f "$src" ]; then
            realpath "$src" 2>/dev/null \
                || readlink -f "$src" 2>/dev/null \
                || echo "$src"
            return
        fi
    fi
    if [ -f "$EM_ROOT/setup-project.sh" ]; then
        realpath "$EM_ROOT/setup-project.sh" 2>/dev/null \
            || readlink -f "$EM_ROOT/setup-project.sh" 2>/dev/null \
            || echo "$EM_ROOT/setup-project.sh"
    fi
}
SETUP_CMD=$(_locate_setup)

# --- First encounter: auto-init via setup-project.sh ---
# why: count file absent == first time; subsequent calls fall through to nudge.
if [ ! -f "$COUNT_FILE" ] && [ -n "$SETUP_CMD" ] && [ -f "$SETUP_CMD" ]; then
    echo "EasyMem: first session in this project — initializing .easymem/ ..."
    if bash "$SETUP_CMD" "$CLAUDE_PROJECT_DIR"; then
        echo 1 > "$COUNT_FILE"
        exit 0
    fi
    echo ""
    echo "(EasyMem auto-setup failed — manual instructions below.)"
fi

# --- Nudge with rising suppress hint ---
COUNT=$(( $(cat "$COUNT_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$COUNT" > "$COUNT_FILE"

cat <<NUDGE
This project does not have EasyMem set up.
To enable persistent knowledge graph memory, run:
  '$SETUP_CMD' '$CLAUDE_PROJECT_DIR'

This adds a .easymem/ directory (gitignored) with a CLI bridge
for keyword search across conversations.
NUDGE

if [ "$COUNT" -ge 3 ]; then
    cat <<SUPPRESS

Seen this nudge $COUNT times? To stop nudging for this project, run:
  easymem nudge suppress
SUPPRESS
fi

exit 0
