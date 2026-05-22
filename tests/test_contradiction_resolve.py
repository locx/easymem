"""Auto-resolution of contradictions under conservative gates.

Detector contract (see semantic_server/maintenance_utils.py):
- Input: iterable of entity dicts with `name` + `observations`.
- Output: {entity_name: [[i, j, jaccard], ...]} — pair indices into
  the *truncated* observation list (last _MAX_OBS_PER_ENTITY=20 obs).

Per-observation source/timestamp do not exist in the data model;
all observations within one entity share the entity-level `_source`.
The resolver therefore:
- Only auto-resolves entities whose `_source` is `episode:*`
  (user-authored memories stay for human review).
- Treats the higher-index observation as newer (append-only ordering).
- Supersedes the older observation in-place with a `superseded: `
  prefix.
"""
import json
import subprocess
import sys
from pathlib import Path

from semantic_server.maintenance_utils import (
    detect_contradictions,
    resolve_contradictions,
)


def _episode_entity(name, obs):
    return {
        "type": "entity",
        "name": name,
        "entityType": "component",
        "observations": list(obs),
        "_source": "episode:claude:1",
        "_created": "2026-05-01T00:00:00Z",
        "_updated": "2026-05-15T00:00:00Z",
    }


def _user_entity(name, obs):
    return {
        "type": "entity",
        "name": name,
        "entityType": "component",
        "observations": list(obs),
        "_source": "user:remember",
        "_created": "2026-05-10T00:00:00Z",
        "_updated": "2026-05-10T00:00:00Z",
    }


def test_newer_observation_supersedes_older_under_episode_source():
    ent = _episode_entity(
        "SyncManager",
        [
            "SyncManager uses LWW conflict resolution",
            "SyncManager no longer uses LWW conflict resolution",
        ],
    )
    entities = [ent]
    findings = detect_contradictions(entities)
    assert "SyncManager" in findings

    resolved, unresolved = resolve_contradictions(entities, findings)
    assert resolved == 1
    assert unresolved == 0

    obs = ent["observations"]
    assert any(o.startswith("superseded: ") and "LWW" in o for o in obs)
    # The negation (later append) survives without prefix.
    assert any(
        "no longer" in o and not o.startswith("superseded: ")
        for o in obs
    )
    # Sidecar findings should be cleared for the resolved entity.
    assert "SyncManager" not in findings


def test_user_source_not_auto_resolved():
    ent = _user_entity(
        "Cache",
        [
            "Cache uses 60 second TTL",
            "Cache does not use 60 second TTL",
        ],
    )
    entities = [ent]
    findings = detect_contradictions(entities)
    assert "Cache" in findings

    resolved, unresolved = resolve_contradictions(entities, findings)
    assert resolved == 0
    assert unresolved >= 1
    # Sidecar findings preserved for human review.
    assert "Cache" in findings
    # Observations untouched.
    assert not any(o.startswith("superseded: ") for o in ent["observations"])


def test_maintenance_persists_resolution(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    line = json.dumps({
        "type": "entity",
        "name": "SyncManager",
        "entityType": "component",
        "observations": [
            "SyncManager uses LWW conflict resolution",
            "SyncManager no longer uses LWW conflict resolution",
        ],
        "_source": "episode:claude:1",
        "_created": "2026-05-01T00:00:00Z",
        "_updated": "2026-05-15T00:00:00Z",
    })
    (mem / "graph.jsonl").write_text(line + "\n")

    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(root / "maintenance.py"),
         str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    text = (mem / "graph.jsonl").read_text()
    assert "superseded: " in text
    # Sidecar is unlinked when findings empty; assert that invariant.
    sidecar = mem / "contradictions.json"
    assert not sidecar.exists()
