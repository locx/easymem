"""Decay must honor a minimum-age floor, a per-pass prune cap, a
non-zero first-recall boost, and merge delta lines before scoring —
otherwise young or healthy entities get deleted unrecoverably.
"""
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import maintenance
from semantic_server.maintenance_utils import prune_entities, score_entity


def _iso(days_ago):
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entity(name, days_ago, n_obs=1):
    return {
        "type": "entity", "name": name, "entityType": "component",
        "observations": [f"{name} obs {i}" for i in range(n_obs)],
        "_created": _iso(days_ago), "_updated": _iso(days_ago),
    }


def test_day10_entity_survives_prune():
    kept, _, pruned = prune_entities([_entity("Young", 10)], [])
    assert pruned == 0
    assert kept and kept[0]["name"] == "Young"


def test_prune_capped_per_pass():
    ents = [_entity(f"Stale{i}", 120) for i in range(150)]
    kept, _, pruned = prune_entities(ents, [])
    assert pruned <= 100
    assert len(kept) >= 50


def test_first_recall_boosts_score():
    now_ts = time.time()
    ent = _entity("E", 40)
    base = score_entity(ent, now_ts, {})
    boosted = score_entity(ent, now_ts, {"E": 1})
    assert boosted > base


def test_delta_lines_merge_before_scoring(tmp_path):
    # why: an entity split across append deltas must be scored as one
    # merged record, not pruned line-by-line.
    mem = tmp_path / ".easymem"
    mem.mkdir()
    ents = [
        _entity("Split", 35, n_obs=2),
        {**_entity("Split", 35), "observations": ["delta a", "delta b"]},
    ]
    entities, _, _, _, _ = maintenance._compute_maintenance(
        ents, [], str(tmp_path), str(mem)
    )
    names = [e.get("name") for e in entities]
    assert "Split" in names


def test_backup_contains_pre_prune_state(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    lines = [
        ('{"type":"entity","name":"OldPrunable","entityType":"component",'
         f'"observations":["stale"],"_created":"{_iso(60)}",'
         f'"_updated":"{_iso(60)}"}}'),
        ('{"type":"entity","name":"FreshKeeper","entityType":"component",'
         '"observations":["a","b","c","d"],'
         f'"_created":"{_iso(0)}","_updated":"{_iso(0)}"}}'),
    ]
    graph = mem / "graph.jsonl"
    graph.write_text("\n".join(lines) + "\n")
    maintenance.run(str(tmp_path), force=True)
    assert "OldPrunable" not in graph.read_text()
    bak = Path(str(graph) + ".bak")
    assert bak.exists()
    assert "OldPrunable" in bak.read_text()
