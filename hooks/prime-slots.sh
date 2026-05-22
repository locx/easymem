#!/usr/bin/env bash
# Inject pinned slots into the session start context.
set -euo pipefail
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
MEM_DIR="${PROJECT_DIR}/.easymem"
if [ ! -f "${MEM_DIR}/slots.json" ]; then
  exit 0
fi
python3 - <<'PY'
import json
import os
import sys

path = os.path.join(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()),
                    ".easymem", "slots.json")
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)
lines = []
for key in ("persona", "preferences", "guidelines"):
    val = (data.get(key) or "").strip()
    if val:
        lines.append(f"# {key}\n{val}")
if lines:
    print("# easymem pinned slots\n\n" + "\n\n".join(lines))
PY
