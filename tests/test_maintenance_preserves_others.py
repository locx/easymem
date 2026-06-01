import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_maintenance_preserves_other_rows_when_resolving(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    entity = {
        "type": "entity", "name": "Svc", "entityType": "note",
        "_source": "episode:abc",
        "_created": "2026-06-01T00:00:00Z",
        "_updated": "2026-06-01T00:00:00Z",
        "observations": [
            "the cache layer uses redis for session storage",
            "the cache layer does not use redis for session storage",
        ],
    }
    other = {"type": "meta", "note": "KEEPME"}
    (mem / "graph.jsonl").write_text(
        json.dumps(entity) + "\n" + json.dumps(other) + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, str(ROOT / "maintenance.py"),
         str(tmp_path), "--force"],
        check=True, capture_output=True,
    )

    content = (mem / "graph.jsonl").read_text(encoding="utf-8")
    # why: contradiction resolution must not drop non-entity/relation rows.
    assert "KEEPME" in content
    assert "superseded: the cache layer uses redis" in content
