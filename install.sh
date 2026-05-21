#!/bin/bash
# EasyMem Infrastructure — Installer
# Deploys runtime to ~/.claude/easymem/ and hooks to ~/.claude/hooks/.
# Run from the easymem project directory.
# Flags: --no-vector  --no-hooks  --minimal (both)
set -euo pipefail

VECTOR_INSTALL=1
WIRE_HOOKS=1

usage() {
    cat <<EOF
Usage: install.sh [--no-vector] [--no-hooks] [--minimal]

Default: installs everything (vector retrieval + hooks). Flags opt out.
EOF
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --no-vector) VECTOR_INSTALL=0 ;;
        --no-hooks)  WIRE_HOOKS=0 ;;
        --minimal)   VECTOR_INSTALL=0; WIRE_HOOKS=0 ;;
        --help|-h)   usage ;;
        *) echo "Unknown argument: $1" >&2; usage ;;
    esac
    shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_HOME="${HOME}/.claude"
EASYMEM_DIR="${CLAUDE_HOME}/easymem"
HOOKS_DIR="${CLAUDE_HOME}/hooks"
SETTINGS="${CLAUDE_HOME}/settings.json"

echo "=== EasyMem Infrastructure Installer ==="
echo "  Source: ${SCRIPT_DIR}"
echo "  Vector: $([ $VECTOR_INSTALL -eq 1 ] && echo ON || echo OFF)"
echo "  Hooks:  $([ $WIRE_HOOKS -eq 1 ] && echo ON || echo OFF)"
echo ""

# --- Step 1: Preflight (python3 3.10+, git) ---
echo "[1/5] Preflight..."

check_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "  ERROR: $1 not found. Install it first." >&2
        exit 1
    }
    echo "  [ok] $1"
}
check_cmd python3
check_cmd git

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "  ERROR: Python 3.10+ required" >&2
    exit 1
}
echo "  [ok] Python 3.10+"
echo ""

# --- Step 2: Create install directories ---
echo "[2/5] Creating directories..."
mkdir -p "${EASYMEM_DIR}" "${HOOKS_DIR}"
echo "  [ok] ${EASYMEM_DIR}/"
echo "  [ok] ${HOOKS_DIR}/"
echo ""

# --- Step 3: Deploy runtime to ~/.claude/easymem/ ---
echo "[3/5] Deploying runtime..."

# Clean copy avoids stale __pycache__ binding to a prior tree layout.
rm -rf "${EASYMEM_DIR}/semantic_server"
cp -r "${SCRIPT_DIR}/semantic_server" "${EASYMEM_DIR}/semantic_server"
find "${EASYMEM_DIR}/semantic_server" -name '__pycache__' -type d \
    -exec rm -rf {} + 2>/dev/null || true
echo "  [ok] semantic_server/"

for f in maintenance.py easymem-cli.py easymem; do
    cp "${SCRIPT_DIR}/${f}" "${EASYMEM_DIR}/${f}"
    chmod +x "${EASYMEM_DIR}/${f}"
    echo "  [ok] ${f}"
done

printf '%s' "${SCRIPT_DIR}" > "${EASYMEM_DIR}/.source-dir"
echo ""

# --- Step 4: Vector retrieval venv + model ---
if [ $VECTOR_INSTALL -eq 1 ]; then
    echo "[4/5] Setting up vector retrieval..."
    VENV_DIR="${EASYMEM_DIR}/venv"
    VENV_PY="${VENV_DIR}/bin/python3"

    if [ ! -x "$VENV_PY" ]; then
        python3 -m venv "$VENV_DIR"
        echo "  [ok] venv at ${VENV_DIR}"
    else
        echo "  [skip] venv already exists"
    fi

    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install --quiet -r "${SCRIPT_DIR}/requirements.txt" || {
        echo "  ERROR: pip install failed; re-run install.sh to retry" >&2
        exit 1
    }
    echo "  [ok] requirements.txt installed"

    # One interpreter spawn: preload model AND read SHA for the manifest.
    # HF cache is content-addressed, so a partial download won't poison retry.
    MODEL_REV=$("$VENV_PY" -c '
from model2vec import StaticModel
from huggingface_hub import HfApi
StaticModel.from_pretrained("minishlab/potion-retrieval-32M")
try:
    print(HfApi().model_info("minishlab/potion-retrieval-32M").sha or "")
except Exception:
    print("")
' 2>/dev/null) || {
        echo "  ERROR: model preload failed; re-run install.sh to retry" >&2
        exit 1
    }

    printf '%s\n' "$VENV_PY" > "${EASYMEM_DIR}/.venv-python"

    MODEL_REV="$MODEL_REV" VENV_DIR="$VENV_DIR" \
        MANIFEST="${EASYMEM_DIR}/.install-manifest" python3 - <<'PYEOF'
import json, os, time
data = {
    "model": "minishlab/potion-retrieval-32M",
    "model_rev": os.environ["MODEL_REV"],
    "venv": os.environ["VENV_DIR"],
    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
with open(os.environ["MANIFEST"], "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PYEOF
    echo "  [ok] model + manifest"
    echo ""
else
    echo "[4/5] [skip] vector retrieval (--no-vector)"
    echo "  Hybrid search disabled — TF-IDF only."
    echo ""
fi

# --- Step 5: Deploy hooks + wire settings.json ---
echo "[5/5] Deploying hooks..."

HOOKS=(prime-easymem.sh prime-on-compact.sh capture-decisions.sh
       nudge-setup.sh capture-tool-context.sh
       capture_tool_context.py smart_recall.py)
for h in "${HOOKS[@]}"; do
    cp "${SCRIPT_DIR}/hooks/${h}" "${HOOKS_DIR}/${h}"
done
chmod +x "${HOOKS_DIR}"/*.sh "${HOOKS_DIR}"/*.py
echo "  [ok] ${#HOOKS[@]} hooks → ${HOOKS_DIR}/"

if [ $WIRE_HOOKS -eq 1 ]; then
    # _hook_merge.py handles missing settings.json by starting from {}.
    # Format: "<event> <hook-basename> <timeout>"
    HOOK_WIRING=(
        "SessionStart prime-easymem.sh 10"
        "SessionStart nudge-setup.sh 3"
        "PostToolUse capture-tool-context.sh 3"
        "Stop capture-decisions.sh 3"
        "PreCompact prime-on-compact.sh 5"
    )
    for spec in "${HOOK_WIRING[@]}"; do
        # shellcheck disable=SC2086
        set -- $spec
        python3 "${SCRIPT_DIR}/scripts/_hook_merge.py" --mode add \
            --settings "${SETTINGS}" --event "$1" \
            --hook-file "\$HOME/.claude/hooks/$2" --timeout "$3"
    done
    echo "  [ok] wired in ${SETTINGS}"
else
    echo "  [skip] hook wiring (--no-hooks)"
    echo "  Hooks deployed but not wired. Re-run without --no-hooks."
fi

cat <<EOF

============================================================
Installation complete.

  Runtime:   ${EASYMEM_DIR}/
  Hooks:     ${HOOKS_DIR}/
  Settings:  ${SETTINGS}

To use 'easymem' globally:
    export PATH="\$HOME/.claude/easymem:\$PATH"

To set up a project:
    ${SCRIPT_DIR}/setup-project.sh /path/to/project

Restart Claude Code to activate hooks.
============================================================
EOF
