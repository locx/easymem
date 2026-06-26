#!/bin/bash
# Usage: setup-project.sh [project_dir]
# Safe to re-run — skips existing files, merges configs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${1:-$(pwd)}"
PROJECT_DIR="$(cd "$PROJECT_DIR" 2>/dev/null && pwd)" || {
    echo "ERROR: Directory not found: ${1:-.}"
    exit 1
}
EASYMEM_DIR="${PROJECT_DIR}/.easymem"
GITIGNORE="${PROJECT_DIR}/.gitignore"
CLAUDE_HOME="${HOME}/.claude"
# why: prefer plugin runtime if installed via /plugin marketplace; fall back
# to the legacy install.sh location so install.sh installs continue to work.
EM_ROOT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_HOME}/easymem}"
EASYMEM_PY="$(cat "${EM_ROOT}/.venv-python" 2>/dev/null || echo python3)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"

echo "=== EasyMem Setup: ${PROJECT_DIR} ==="

if [ ! -f "${EM_ROOT}/maintenance.py" ]; then
    echo "ERROR: EasyMem runtime not found at ${EM_ROOT}/"
    SOURCE_DIR_FILE="${EM_ROOT}/.source-dir"
    if [ -f "$SOURCE_DIR_FILE" ]; then
        echo "Run: $(cat "$SOURCE_DIR_FILE")/install.sh first"
    else
        echo "Run install.sh from the easymem project, or install the plugin first"
    fi
    exit 1
fi

# ---- 1. Create .easymem directory ----
mkdir -p "${EASYMEM_DIR}"
echo "  [ok] ${EASYMEM_DIR}/"

# ---- 1b. Stable wrapper at ~/.claude/easymem-bin/easymem ----
# why: plugin cache path moves on update; a fixed wrapper gives CLAUDE.md
# docs and the Bash permission a single stable invocation point.
WRAPPER_DIR="${CLAUDE_HOME}/easymem-bin"
WRAPPER_PATH="${WRAPPER_DIR}/easymem"
mkdir -p "${WRAPPER_DIR}"
cat > "${WRAPPER_PATH}" <<WRAPEOF
#!/bin/bash
# Forwards to the easymem runtime resolved at install time.
exec "${EM_ROOT}/easymem" "\$@"
WRAPEOF
chmod +x "${WRAPPER_PATH}"
echo "  [ok] Wrapper: ${WRAPPER_PATH} -> ${EM_ROOT}/easymem"

# ---- 2. Create config.json template ----
CONFIG_FILE="${EASYMEM_DIR}/config.json"
if [ ! -f "${CONFIG_FILE}" ]; then
    cat > "${CONFIG_FILE}" << 'CFGEOF'
{
  "_comment": "Optional overrides — delete keys to use defaults",
  "decay_threshold": 0.1,
  "max_age_days": 90,
  "min_prune_age_days": 30,
  "throttle_hours": 24,
  "min_merge_name_len": 4
}
CFGEOF
    echo "  [ok] ${CONFIG_FILE} (template)"
else
    echo "  [skip] ${CONFIG_FILE} — already exists"
fi

# ---- 3. Bootstrap empty graph.jsonl ----
GRAPH_FILE="${EASYMEM_DIR}/graph.jsonl"
if [ ! -f "${GRAPH_FILE}" ]; then
    touch "${GRAPH_FILE}"
    echo "  [ok] ${GRAPH_FILE} (empty)"
else
    echo "  [skip] ${GRAPH_FILE} — already exists"
fi

# ---- 4. Add .easymem/ to .gitignore ----
if [ -f "${GITIGNORE}" ]; then
    if grep -qxF '.easymem/' "${GITIGNORE}" 2>/dev/null; then
        echo "  [skip] .easymem/ already in .gitignore"
    else
        printf '\n# Memory\n.easymem/\n' >> "${GITIGNORE}"
        echo "  [ok] Added .easymem/ to .gitignore"
    fi
else
    printf '# Memory\n.easymem/\n' > "${GITIGNORE}"
    echo "  [ok] Created .gitignore with .easymem/"
fi

# ---- 5. Migrate built-in auto-memory into graph ----
# why: salvage Claude Code's pre-existing auto-memory so nothing is lost.
AUTO_MEM_KEY=$(echo "${PROJECT_DIR}" | sed 's|^/||; s|/|-|g')
AUTO_MEM_DIR="${CLAUDE_HOME}/projects/-${AUTO_MEM_KEY}/memory"

if [ -d "${AUTO_MEM_DIR}" ]; then
    python3 "${SCRIPT_DIR}/scripts/_migrate_auto_memory.py" \
        "${AUTO_MEM_DIR}" "${GRAPH_FILE}"
else
    echo "  [skip] No built-in auto-memory found for this project"
fi

# ---- 6. Build TF-IDF index if graph has data ----
if [ -s "${GRAPH_FILE}" ]; then
    echo ""
    echo "[index] Building TF-IDF index..."
    "${EASYMEM_PY}" "${EM_ROOT}/maintenance.py" "${PROJECT_DIR}" --force 2>/dev/null || true
    if [ -f "${EASYMEM_DIR}/tfidf_index.json" ]; then
        IDX_KB=$(( $(wc -c < "${EASYMEM_DIR}/tfidf_index.json" | tr -d ' ') / 1024 ))
        echo "  [ok] TF-IDF index: ${IDX_KB}KB"
    fi
fi

# ---- 6b. Per-project model preload (validates project-specific embed_model).
# Soft-fail: a bad embed_model in config.json should not abort setup with the
# project half-initialized. Hybrid search still works once corrected + rebuilt.
# Read embed_model via env-var hand-off so a hostile value in config.json
# can't break out of the python literal context (shell injection).
PROJECT_MODEL=$(EASYMEM_DIR="$EASYMEM_DIR" python3 -c '
import json, os
try:
    with open(os.path.join(os.environ["EASYMEM_DIR"], "config.json")) as f:
        cfg = json.load(f)
    print(cfg.get("embed_model", "minishlab/potion-retrieval-32M"))
except Exception:
    print("minishlab/potion-retrieval-32M")
' 2>/dev/null)

if [ -x "$EASYMEM_PY" ] && [ "$EASYMEM_PY" != "python3" ]; then
    # Probe model2vec separately so a missing dep can be distinguished
    # from a bad embed_model name (different remediation).
    if ! "$EASYMEM_PY" -c 'import model2vec' 2>/dev/null; then
        echo "  [skip] model2vec not installed in venv — re-run install.sh"
        echo "         without --no-vector to enable hybrid search"
    elif PROJECT_MODEL="$PROJECT_MODEL" "$EASYMEM_PY" -c '
import os
from model2vec import StaticModel
m = os.environ["PROJECT_MODEL"]
StaticModel.from_pretrained(m)
print(f"  [ok] embed model ready: {m}")
' 2>/dev/null; then
        :
    else
        echo "  [warn] could not preload embed model: ${PROJECT_MODEL}"
        echo "         fix embed_model in ${EASYMEM_DIR}/config.json, then:"
        echo "         '${WRAPPER_PATH}' rebuild"
    fi
else
    echo "  [skip] vector setup — run install.sh first to enable hybrid search"
fi

# ---- 7. Add Bash permission for easymem commands in .claude/settings.json ----
# why: read-only subcommands only — a blanket `easymem *` would
# auto-approve graph writes and deletes without a prompt.
PROJ_SETTINGS_DIR="${PROJECT_DIR}/.claude"
PROJ_SETTINGS="${PROJ_SETTINGS_DIR}/settings.json"

mkdir -p "${PROJ_SETTINGS_DIR}"
python3 - "${PROJ_SETTINGS}" << 'PYEOF'
import json, os, sys

settings_path = sys.argv[1]
base = 'Bash($HOME/.claude/easymem-bin/easymem'
new_perms = [f'{base} {sub} *)' for sub in
             ('search', 'recall', 'status', 'doctor', 'diff', 'help')]
legacy = f'{base} *)'

try:
    with open(settings_path, encoding='utf-8') as f:
        cfg = json.load(f)
except (OSError, json.JSONDecodeError, ValueError):
    cfg = {}

perms = cfg.setdefault('permissions', {})
allow = perms.setdefault('allow', [])

changed = False
if legacy in allow:
    allow.remove(legacy)
    changed = True
for perm in new_perms:
    if perm not in allow:
        allow.append(perm)
        changed = True

if not changed:
    print(f'  [skip] easymem Bash permissions already in {settings_path}')
    sys.exit(0)

tmp = settings_path + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, settings_path)
print(f'  [ok] Added easymem read-only Bash permissions to {settings_path}')
PYEOF

# ---- 8. Add/update EasyMem plugin instructions in CLAUDE.md ----
CLAUDE_MD="${PROJECT_DIR}/CLAUDE.md"
EASYMEM_MARKER="## EasyMem (Easy Memory)"

EASYMEM_SECTION='## EasyMem (Easy Memory)

Knowledge graph in `.easymem/` — entities, relations, decisions. Searchable via TF-IDF, branch-aware, with decay. A SessionStart hook prints a compact status line automatically (~50 tokens).

All commands use `$HOME/.claude/easymem-bin/easymem`:

### Read

- `$HOME/.claude/easymem-bin/easymem search <query>` — find entities by topic (use before writing to avoid duplicates)
- `$HOME/.claude/easymem-bin/easymem recall <query>` — search + 1-hop graph neighbors (use to understand connections)
- `$HOME/.claude/easymem-bin/easymem diff` — what changed since last session
- `$HOME/.claude/easymem-bin/easymem status` — graph health + pending decision nudge

### Write

- `$HOME/.claude/easymem-bin/easymem write '\''{"entities":[{"name":"AuthService","entityType":"service","observations":["Handles JWT tokens"]}]}'\''`
- `$HOME/.claude/easymem-bin/easymem write '\''{"relations":[{"from":"AuthService","to":"UserDB","relationType":"depends-on"}]}'\''`
- `$HOME/.claude/easymem-bin/easymem decide '\''{"title":"Use Postgres over Mongo","rationale":"need multi-doc txns","alternatives":["MongoDB -- no txns"]}'\''`
- `$HOME/.claude/easymem-bin/easymem remove '\''{"entity_names":["OldService"]}'\''`

### Maintain

- `$HOME/.claude/easymem-bin/easymem doctor` — deep health check (orphan relations, stale decisions, index age)
- `$HOME/.claude/easymem-bin/easymem rebuild` — force TF-IDF index refresh

### Rules

1. **Search before write.** Run `$HOME/.claude/easymem-bin/easymem search` before creating entities to avoid duplicates.
2. **Decisions are mandatory.** Major task + architectural choice + no `$HOME/.claude/easymem-bin/easymem decide` = incomplete.
3. **Resolve stale decisions.** If SessionStart shows pending decisions, resolve with `$HOME/.claude/easymem-bin/easymem decide '\''{"action":"resolve","title":"...","outcome":"successful","lesson":"..."}'\''`.
4. **Do not edit `.easymem/` files directly.** Always use `$HOME/.claude/easymem-bin/easymem` commands.
5. **Aliases** in `.easymem/aliases.json` improve search: `{"groups": [["cache", "memoize"], ["api", "endpoint"]]}`'

if [ ! -f "${CLAUDE_MD}" ]; then
    printf '%s\n' "$EASYMEM_SECTION" > "${CLAUDE_MD}"
    echo "  [ok] Created CLAUDE.md with easymem plugin section"
elif grep -qE '## EasyMem \(Easy Memory\)' "${CLAUDE_MD}" 2>/dev/null; then
    python3 "${SCRIPT_DIR}/scripts/_strip_memory_section.py" "${CLAUDE_MD}"
    printf '%s\n' "$EASYMEM_SECTION" >> "${CLAUDE_MD}"
    echo "  [ok] Upgraded easymem plugin section in CLAUDE.md"
else
    printf '%s\n' "$EASYMEM_SECTION" >> "${CLAUDE_MD}"
    echo "  [ok] Added easymem plugin section to CLAUDE.md"
fi

echo ""
echo "============================================================"
echo "  Setup complete: ${PROJECT_NAME}"
echo ""
echo "  Graph:    ${GRAPH_FILE}"
echo "  Wrapper:  ${WRAPPER_PATH}"
echo "  Access:   CLI bridge (Bash) — works in both CLI and VSCode"
echo ""
echo "  Commands:"
echo "    search, recall, write, decide, remove,"
echo "    status, doctor, rebuild, diff"
echo "============================================================"
