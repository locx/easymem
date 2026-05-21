#!/bin/bash
# Cleanup utility — removes easymem artifacts.
# Modes: project [dir] | global | all [dir]
# Options: --yes (skip prompts), --dry-run
# Safe: prompts before each destructive step unless --yes flag.
set -euo pipefail

CLAUDE_HOME="${HOME}/.claude"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: cleanup.sh <mode> [options]"
    echo ""
    echo "Modes:"
    echo "  project [dir]   Remove .easymem/, .gitignore + CLAUDE.md"
    echo "                  entries, and easymem permission from a project"
    echo "                  (defaults to current directory)"
    echo "  global          Remove ~/.claude/easymem/, hooks, settings entries"
    echo "  all [dir]       Both global + project cleanup"
    echo ""
    echo "Options:"
    echo "  --yes           Skip confirmation prompts"
    echo "  --dry-run       Show what would be removed without deleting"
    exit 1
}

# --- Parse arguments ---
MODE=""
PROJECT_DIR=""
AUTO_YES=false
DRY_RUN=false

while [ $# -gt 0 ]; do
    case "$1" in
        project|global|all) MODE="$1" ;;
        --yes) AUTO_YES=true ;;
        --dry-run) DRY_RUN=true ;;
        --help|-h) usage ;;
        *)
            if [ -z "$PROJECT_DIR" ] && [ -d "$1" ]; then
                PROJECT_DIR="$1"
            else
                echo "Unknown argument: $1"
                usage
            fi
            ;;
    esac
    shift
done

[ -z "$MODE" ] && usage

# Resolve project directory to absolute path
if [ "$MODE" = "project" ] || [ "$MODE" = "all" ]; then
    PROJECT_DIR="${PROJECT_DIR:-$(pwd)}"
    PROJECT_DIR="$(cd "$PROJECT_DIR" 2>/dev/null && pwd)" || {
        echo "ERROR: Directory not found: $PROJECT_DIR"
        exit 1
    }
fi

confirm() {
    if $AUTO_YES || $DRY_RUN; then
        return 0
    fi
    printf "${YELLOW}  %s [y/N] ${NC}" "$1"
    read -r ans
    case "$ans" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

remove_path() {
    local path="$1"
    local label="$2"
    if [ -e "$path" ]; then
        if $DRY_RUN; then
            echo "  [dry-run] Would remove: $path"
        else
            rm -rf "$path"
            printf "  ${GREEN}[removed]${NC} %s\n" "$label"
        fi
    else
        echo "  [skip] $label — not found"
    fi
}

# --- Project cleanup ---
# Steps: 1) .easymem/ dir  2) .gitignore entries  3) CLAUDE.md plugin section
#        4) .claude/settings.json easymem permission  5) temp markers
cleanup_project() {
    local dir="$1"
    echo ""
    printf "${RED}=== Project Cleanup: ${dir} ===${NC}\n"

    # 1. Remove .easymem/ directory
    if [ -d "${dir}/.easymem" ]; then
        ENTITY_COUNT=0
        if [ -f "${dir}/.easymem/graph.jsonl" ]; then
            ENTITY_COUNT=$(grep -c '"type":"entity"' "${dir}/.easymem/graph.jsonl" 2>/dev/null || echo 0)
        fi
        echo "  Found .easymem/ with ~${ENTITY_COUNT} entities"
        if confirm "Delete ${dir}/.easymem/ ?"; then
            remove_path "${dir}/.easymem" ".easymem/"
        fi
    else
        echo "  [skip] .easymem/ — not found"
    fi

    # 2. Remove .easymem/ lines from .gitignore
    if [ -f "${dir}/.gitignore" ]; then
        if grep -q '\.easymem/' "${dir}/.gitignore" 2>/dev/null; then
            if $DRY_RUN; then
                echo "  [dry-run] Would remove .easymem/ lines from .gitignore"
            else
                # Remove .easymem/ line and preceding "# Memory" comment
                python3 - "${dir}/.gitignore" << 'PYEOF'
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as f:
    lines = f.readlines()

out = []
skip_next = False
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == '.easymem/' or stripped == '.easymem':
        # Also remove preceding "# Memory" comment
        if out and out[-1].strip() == '# Memory':
            out.pop()
        continue
    out.append(line)

# Remove trailing blank lines
while out and out[-1].strip() == '':
    out.pop()
if out:
    out[-1] = out[-1].rstrip('\n') + '\n'

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(out)
print('  \033[0;32m[removed]\033[0m .easymem/ from .gitignore')
PYEOF
            fi
        else
            echo "  [skip] .gitignore — no .easymem/ entry"
        fi
    fi

    # 3. Remove EasyMem Plugin section from CLAUDE.md
    if [ -f "${dir}/CLAUDE.md" ]; then
        if grep -qE '## EasyMem' "${dir}/CLAUDE.md" 2>/dev/null; then
            if $DRY_RUN; then
                echo "  [dry-run] Would remove EasyMem section from CLAUDE.md"
            elif confirm "Remove EasyMem section from CLAUDE.md?"; then
                python3 "${SCRIPT_DIR}/scripts/_strip_memory_section.py" "${dir}/CLAUDE.md"
                printf '  \033[0;32m[removed]\033[0m EasyMem section from CLAUDE.md\n'
            fi
        else
            echo "  [skip] CLAUDE.md — no EasyMem section"
        fi
    else
        echo "  [skip] CLAUDE.md — not found"
    fi

    # 4. Remove easymem Bash permission from .claude/settings.json (symmetric
    #    with setup-project.sh which adds it).
    PROJ_SETTINGS="${dir}/.claude/settings.json"
    if [ -f "$PROJ_SETTINGS" ]; then
        python3 - "$PROJ_SETTINGS" "$DRY_RUN" << 'PYEOF'
import json, os, sys

path = sys.argv[1]
dry_run = sys.argv[2] == 'true'
target = 'Bash($HOME/.claude/easymem/easymem *)'

try:
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
except (json.JSONDecodeError, ValueError, OSError):
    print('  [skip] .claude/settings.json — unreadable')
    sys.exit(0)

allow = cfg.get('permissions', {}).get('allow', [])
if target not in allow:
    print('  [skip] .claude/settings.json — no easymem permission entry')
    sys.exit(0)

if dry_run:
    print(f'  [dry-run] Would remove permission "{target}"')
    sys.exit(0)

allow.remove(target)
# Tidy up empty containers
if not allow:
    cfg['permissions'].pop('allow', None)
if not cfg.get('permissions'):
    cfg.pop('permissions', None)

if cfg:
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
else:
    os.unlink(path)
    # Remove .claude/ if now empty
    cdir = os.path.dirname(path)
    if os.path.isdir(cdir) and not os.listdir(cdir):
        os.rmdir(cdir)

print('  \033[0;32m[removed]\033[0m easymem permission from .claude/settings.json')
PYEOF
    fi

    # 5. Clear temp nudge marker for this project
    if ! $DRY_RUN; then
        # Compute same hash as nudge-setup.sh
        _proj_hash=""
        if command -v md5 &>/dev/null; then
            _proj_hash=$(md5 -q -s "$dir")
        elif command -v md5sum &>/dev/null; then
            _proj_hash=$(echo -n "$dir" | md5sum | cut -d' ' -f1)
        else
            _proj_hash=$(echo -n "$dir" | cksum | cut -d' ' -f1)
        fi
        rm -f "/tmp/.claude-easymem-nudge-${_proj_hash}" 2>/dev/null || true
    fi
}

# --- Global cleanup ---
# Steps: 1) ~/.claude/easymem/ (incl. venv+sidecar+manifest)
#        2) hook scripts  3) settings.json hook wiring  4) temp markers
cleanup_global() {
    echo ""
    printf "${RED}=== Global Cleanup ===${NC}\n"

    # 1. Remove ~/.claude/easymem/ runtime directory (includes venv, sidecar,
    #    manifest). HF cache at ~/.cache/huggingface kept — shared across installs.
    if [ -d "${CLAUDE_HOME}/easymem" ]; then
        echo "  Found ~/.claude/easymem/"
        if confirm "Delete ${CLAUDE_HOME}/easymem/ ?"; then
            remove_path "${CLAUDE_HOME}/easymem" "~/.claude/easymem/"
        fi
    else
        echo "  [skip] ~/.claude/easymem/ — not found"
    fi

    # 2. Remove deployed hook scripts
    HOOKS=(prime-easymem.sh prime-on-compact.sh capture-decisions.sh nudge-setup.sh capture-tool-context.sh capture_tool_context.py smart_recall.py)
    for hook in "${HOOKS[@]}"; do
        if [ -e "${CLAUDE_HOME}/hooks/${hook}" ]; then
            if confirm "Remove hook ${hook}?"; then
                remove_path "${CLAUDE_HOME}/hooks/${hook}" "~/.claude/hooks/${hook}"
            fi
        else
            echo "  [skip] ~/.claude/hooks/${hook} — not found"
        fi
    done
    # Remove hooks dir if empty
    if [ -d "${CLAUDE_HOME}/hooks" ] && [ -z "$(ls -A "${CLAUDE_HOME}/hooks" 2>/dev/null)" ]; then
        if ! $DRY_RUN; then
            rmdir "${CLAUDE_HOME}/hooks" 2>/dev/null || true
        fi
    fi

    # 3. Remove easymem hook wiring from settings.json
    if [ -f "${CLAUDE_HOME}/settings.json" ]; then
        _dry_flag=""
        if $DRY_RUN; then _dry_flag="--dry-run"; fi
        python3 "${SCRIPT_DIR}/scripts/_hook_merge.py" --mode strip \
            --settings "${CLAUDE_HOME}/settings.json" ${_dry_flag}
    fi

    # 4. Clean up all temp markers
    if $DRY_RUN; then
        echo "  [dry-run] Would remove /tmp/.claude-easymem-* markers"
    elif confirm "Remove /tmp/.claude-easymem-* temp markers?"; then
        rm -f /tmp/.claude-easymem-* 2>/dev/null || true
        echo "  [removed] /tmp/.claude-easymem-* markers"
    fi
}

# --- Execute ---
case "$MODE" in
    project)
        cleanup_project "$PROJECT_DIR"
        ;;
    global)
        cleanup_global
        ;;
    all)
        cleanup_project "$PROJECT_DIR"
        cleanup_global
        ;;
esac

echo ""
if $DRY_RUN; then
    printf "${YELLOW}Dry run complete — no files were modified.${NC}\n"
    echo "Run without --dry-run to apply changes."
else
    printf "${GREEN}Cleanup complete.${NC}\n"
    if [ "$MODE" = "global" ] || [ "$MODE" = "all" ]; then
        echo "Restart Claude Code to apply changes."
        echo "Re-run install.sh + setup-project.sh for a fresh start."
    elif [ "$MODE" = "project" ]; then
        echo "Re-run setup-project.sh to reinitialize this project."
    fi
fi
