#!/bin/bash
# Stop hook: structured decision capture + session summary.
# Global — works for any project with .easymem/ dir.
[ -n "${CLAUDE_PROJECT_DIR:-}" ] || exit 0
[ -d "${CLAUDE_PROJECT_DIR}/.easymem" ] || exit 0

# Guard unset session ID to avoid marker collisions
[ -n "${CLAUDE_SESSION_ID:-}" ] || exit 0
SAFE_SID="${CLAUDE_SESSION_ID//[^a-zA-Z0-9_-]/_}"
MARKER="/tmp/.claude-easymem-reminded-${SAFE_SID}"

# Time-based throttle: re-nudge if >30 min since last
if [ -f "$MARKER" ]; then
    LAST=$(date -r "$MARKER" +%s 2>/dev/null \
        || stat -c%Y "$MARKER" 2>/dev/null \
        || python3 -c "import os,sys; print(int(os.path.getmtime(sys.argv[1])))" "$MARKER" 2>/dev/null \
        || echo 0)
    AGE=$(( $(date +%s) - ${LAST:-0} ))
    if [ "$AGE" -lt 1800 ]; then
        exit 0
    fi
fi

touch "$MARKER"

# Stamp session-start timestamp here (Stop hook) for next session's diff
EASYMEM_DIR="${CLAUDE_PROJECT_DIR}/.easymem"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
EASYMEM_PY="$(cat "${HOME}/.claude/easymem/.venv-python" 2>/dev/null || echo python3)"
LAST_START="${EASYMEM_DIR}/.last-session-start"
python3 -c "import time; open('${LAST_START}','w').write(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))" 2>/dev/null || true

# Count session activity to calibrate prompt
GRAPH="${EASYMEM_DIR}/graph.jsonl"
ACTIVITY=""
if [ -f "$LAST_START" ] && [ -f "$GRAPH" ]; then
    START_TS=$(cat "$LAST_START" 2>/dev/null || echo "")
    if [ -n "$START_TS" ]; then
        # Count entities updated since session start (env-var pattern — no shell interpolation)
        UPDATED=$(GRAPH_PATH="$GRAPH" SESSION_START="$START_TS" python3 - <<'PYEOF'
import json, os, sys
ts = os.environ.get('SESSION_START', '')
graph = os.environ.get('GRAPH_PATH', '')
n = u = 0
try:
    for line in open(graph, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get('type') != 'entity':
                continue
            c = obj.get('_created', '')
            up = obj.get('_updated', '')
            if c and c > ts:
                n += 1
            elif up and up > ts:
                u += 1
        except Exception:
            pass
except Exception:
    pass
print(f'{n},{u}')
PYEOF
        2>/dev/null || echo "0,0")
        NEW_C=$(echo "$UPDATED" | cut -d, -f1)
        UPD_C=$(echo "$UPDATED" | cut -d, -f2)
        # Numeric guards: treat non-numeric values as 0
        NEW_C=$(( ${NEW_C:-0} + 0 )) 2>/dev/null || NEW_C=0
        UPD_C=$(( ${UPD_C:-0} + 0 )) 2>/dev/null || UPD_C=0
        if [ "$NEW_C" -gt 0 ] || [ "$UPD_C" -gt 0 ]; then
            ACTIVITY="This session: +${NEW_C} new entities, ~${UPD_C} updated."
        fi
    fi
fi

NEW_HEAD=$(git -C "$CLAUDE_PROJECT_DIR" rev-parse HEAD 2>/dev/null || echo "")
LAST_HEAD="${EASYMEM_DIR}/.last-session-head"
if [ -n "$NEW_HEAD" ] && [ "$NEW_HEAD" != "$(cat "$LAST_HEAD" 2>/dev/null)" ]; then
    COMMIT_MSG=$(git -C "$CLAUDE_PROJECT_DIR" log -1 --format=%s "$NEW_HEAD" \
          2>/dev/null | head -c 200)
    "${EASYMEM_PY}" "${SCRIPT_DIR}/capture_tool_context.py" --mint-commit \
        "${EASYMEM_DIR}/graph.jsonl" "$NEW_HEAD" "$COMMIT_MSG" 2>/dev/null || true
    echo "$NEW_HEAD" > "$LAST_HEAD"
fi

EM="$HOME/.claude/easymem/easymem"
cat << MSG
SESSION END — persist what you learned:
${ACTIVITY:+$ACTIVITY
}
1. DECISIONS: If you chose between approaches or made architectural
   calls, persist each now:
     $EMdecide '{"title":"what was decided","rationale":"why this approach","alternatives":["rejected option -- reason"],"scope":"affected code area"}'

2. OUTCOMES: If you revisited a prior decision and saw it succeed or
   fail, close the loop:
     $EMdecide '{"action":"resolve","title":"prior decision","outcome":"successful","lesson":"what we learned"}'

3. WARNINGS: If you found gotchas, fragile code, or foot-guns:
     $EMwrite '{"entities":[{"name":"filename.py","entityType":"file-warning","observations":["[WARNING] description"]}]}'

4. PATTERNS: If you discovered reusable knowledge (conventions,
   integration points, API quirks), persist as entities + relations:
     $EMwrite '{"entities":[...],"relations":[...]}'

Skip any that don't apply. Only persist what's genuinely useful.
MSG
exit 0
