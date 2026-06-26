#!/bin/bash
# Import an EasyMem export bundle into a project's graph.
# Usage: import-easymem.sh <bundle_file> [project_dir]
#
# Merges entities from the bundle, deduplicating by name+type.
# Creates a backup before merging.
# Passes paths via sys.argv — no shell interpolation into Python.
# Validates imported entity structure.
set -euo pipefail

BUNDLE="${1:?Usage: import-easymem.sh <bundle_file> [project_dir]}"
PROJECT_DIR="${2:-$(pwd)}"
PROJECT_DIR="$(cd "$PROJECT_DIR" 2>/dev/null && pwd)" || {
    echo "ERROR: Directory not found: ${2:-.}"
    exit 1
}
EASYMEM_DIR="${EASYMEM_DIR:-${PROJECT_DIR}/.easymem}"
GRAPH="${EASYMEM_DIR}/graph.jsonl"

if [ ! -f "$BUNDLE" ]; then
    echo "ERROR: Bundle not found: ${BUNDLE}"
    exit 1
fi

if [ ! -d "$EASYMEM_DIR" ]; then
    echo "ERROR: Project not initialized. Run setup-project.sh first."
    exit 1
fi

# Reject bundles larger than 50MB
BUNDLE_SIZE=$(wc -c < "$BUNDLE" | tr -d ' ')
if [ "$BUNDLE_SIZE" -gt 52428800 ]; then
    echo "ERROR: Bundle too large (${BUNDLE_SIZE} bytes, max 50MB)"
    exit 1
fi

# Backup existing graph
# why: timestamped — repeated imports must not clobber the prior backup.
if [ -f "$GRAPH" ]; then
    BACKUP="${GRAPH}.pre-import-$(date +%s).bak"
    cp "$GRAPH" "$BACKUP"
    echo "Backed up existing graph to ${BACKUP}"
fi

python3 - "$BUNDLE" "$GRAPH" << 'PYEOF'
import json, os, re, sys, time

bundle_path = sys.argv[1]
graph_path = sys.argv[2]

# why: imported bundles are untrusted — scrub observations with the same
# patterns capture_tool_context.py applies before anything hits the graph.
_SECRET_RE = re.compile(
    r"AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[0-9A-Za-z]{20,}"
    r"|sk-[0-9A-Za-z_\-]{20,}"
    r"|xox[abpros]-[0-9A-Za-z\-]{10,}"
    r"|Bearer\s+[A-Za-z0-9._~+/=\-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|--(?:password|passwd|token|api-key|secret)(?:=|[ \t]+)\S+"
    r"|\b[A-Z_]*(?:PASSWORD|PASSWD|TOKEN|SECRET|API_KEY|ACCESS_KEY)"
    r"[A-Z0-9_]*=\S+"
)
_URL_CRED_RE = re.compile(
    r"(\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:)[^/\s@]+(?=@)"
)
# why: mirror semantic_server.config.MAX_OBS_LENGTH and maintenance's
# durable per-entity cap so imports can't exceed normal-path bounds.
MAX_OBS_LEN = 5000
MAX_OBS_PER_ENTITY = 200


def _sanitize_obs(obs_list):
    """Scrub secrets and bound size; truncate rather than reject."""
    out = []
    for o in obs_list[:MAX_OBS_PER_ENTITY]:
        if isinstance(o, str):
            # why: scrub before truncating so a secret straddling the
            # cut can't survive as an unmatched partial.
            o = _SECRET_RE.sub('[REDACTED]', o)
            o = _URL_CRED_RE.sub(r'\g<1>[REDACTED]', o)
            o = o[:MAX_OBS_LEN]
        out.append(o)
    return out

# Load bundle
with open(bundle_path, encoding='utf-8') as f:
    bundle = json.load(f)

fmt = bundle.get('format', '')
if fmt not in ('easymem-export',):
    print('ERROR: Not a valid EasyMem export bundle')
    sys.exit(1)

import_entries = bundle.get('entries', [])
if not import_entries:
    print('Bundle is empty, nothing to import.')
    sys.exit(0)

# Free bundle metadata — only entries needed
del bundle


def _validate_entity(entry):
    """Validate entity has required fields + correct types."""
    name = entry.get('name')
    if not name or not isinstance(name, str):
        return False
    if not isinstance(entry.get('entityType', ''), str):
        return False
    obs = entry.get('observations', [])
    if not isinstance(obs, list):
        return False
    return True


def _validate_relation(entry):
    """Validate relation has required fields."""
    fr = entry.get('from')
    to = entry.get('to')
    if not fr or not isinstance(fr, str):
        return False
    if not to or not isinstance(to, str):
        return False
    if not isinstance(entry.get('relationType', ''), str):
        return False
    return True


# Load existing graph
existing = []
existing_entities = {}  # (name, type) -> index
existing_rels = set()   # (from, to, relationType)

if os.path.exists(graph_path):
    with open(graph_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        existing.append(obj)
                        if obj.get('type') == 'entity':
                            key = (obj.get('name', ''),
                                   obj.get('entityType', ''))
                            existing_entities[key] = (
                                len(existing) - 1
                            )
                        elif obj.get('type') == 'relation':
                            rk = (obj.get('from', ''),
                                  obj.get('to', ''),
                                  obj.get('relationType', ''))
                            existing_rels.add(rk)
                except json.JSONDecodeError:
                    continue

added_e = 0
merged_e = 0
added_r = 0
skipped_r = 0
skipped_invalid = 0

for entry in import_entries:
    if not isinstance(entry, dict):
        skipped_invalid += 1
        continue
    t = entry.get('type')

    if t == 'entity':
        # Validate structure
        if not _validate_entity(entry):
            skipped_invalid += 1
            continue
        entry['observations'] = _sanitize_obs(
            entry.get('observations', [])
        )
        key = (entry.get('name', ''),
               entry.get('entityType', ''))
        if key in existing_entities:
            # Merge observations (deduplicate)
            idx = existing_entities[key]
            old_obs_raw = existing[idx].get(
                'observations', []
            )
            # Handle unhashable obs (dicts/lists)
            old_obs_str = set()
            for o in old_obs_raw:
                if isinstance(o, str):
                    old_obs_str.add(o)
                else:
                    old_obs_str.add(json.dumps(
                        o, sort_keys=True
                    ))
            new_obs = entry.get('observations', [])
            for obs in new_obs:
                k = obs if isinstance(obs, str) \
                    else json.dumps(obs, sort_keys=True)
                if k not in old_obs_str:
                    existing[idx].setdefault(
                        'observations', []
                    ).append(obs)
                    old_obs_str.add(k)
            # why: keep the tail like maintenance's durable cap does.
            merged_obs = existing[idx].get('observations', [])
            if len(merged_obs) > MAX_OBS_PER_ENTITY:
                existing[idx]['observations'] = (
                    merged_obs[-MAX_OBS_PER_ENTITY:]
                )
            merged_e += 1
        else:
            existing_entities[key] = len(existing)
            existing.append(entry)
            added_e += 1

    elif t == 'relation':
        # Validate structure
        if not _validate_relation(entry):
            skipped_invalid += 1
            continue
        rk = (entry.get('from', ''),
              entry.get('to', ''),
              entry.get('relationType', ''))
        if rk not in existing_rels:
            existing_rels.add(rk)
            existing.append(entry)
            added_r += 1
        else:
            skipped_r += 1
    else:
        sys.stderr.write(
            f'  [warn] Skipping entry with unknown type {t!r}\n'
        )
        skipped_invalid += 1

# why: drop relations whose endpoints no longer exist in the merged set —
# bundles can declare relations referencing entities that were validation-
# rejected or never present, leaving dangling refs.
merged_names = {
    e.get('name', '') for e in existing
    if e.get('type') == 'entity'
}
pruned_existing = []
dropped_dangling = 0
for e in existing:
    if e.get('type') == 'relation':
        if (e.get('from') not in merged_names
                or e.get('to') not in merged_names):
            dropped_dangling += 1
            continue
    pruned_existing.append(e)
existing = pruned_existing

# Write merged graph atomically
tmp = graph_path + '.tmp.import'
try:
    with open(tmp, 'w', encoding='utf-8') as f:
        f.writelines(
            json.dumps(e, separators=(',', ':'))
            + '\n' for e in existing
        )
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, graph_path)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise

print(f'Import complete from: {bundle_path}')
print(f'  Entities: {added_e} added, {merged_e} merged')
print(f'  Relations: {added_r} added, '
      f'{skipped_r} duplicates skipped')
if skipped_invalid:
    print(f'  Skipped {skipped_invalid} invalid entries')
if dropped_dangling:
    print(f'  Dropped {dropped_dangling} relations with missing endpoints')
print(f'  Total entries now: {len(existing)}')
PYEOF
