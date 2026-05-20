from datetime import datetime, timezone, timedelta

from semantic_server.maintenance_utils import prune_entities


def _episode(name, age_days):
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "name": name, "entityType": "episode",
        "observations": ["[ERROR] something"],
        "_created": ts, "_updated": ts,
    }


def test_unrecalled_old_episode_pruned():
    e = _episode("episode:err:old", age_days=20)
    kept, *_ = prune_entities([e], [], recall_counts={})
    names = [k.get("name") for k in kept]
    assert "episode:err:old" not in names


def test_recalled_old_episode_survives():
    e = _episode("episode:err:hot", age_days=20)
    kept, *_ = prune_entities([e], [], recall_counts={"episode:err:hot": 5})
    names = [k.get("name") for k in kept]
    assert "episode:err:hot" in names


def test_young_episode_survives():
    e = _episode("episode:err:young", age_days=3)
    kept, *_ = prune_entities([e], [], recall_counts={})
    names = [k.get("name") for k in kept]
    assert "episode:err:young" in names
