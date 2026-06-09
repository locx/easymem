"""Procedural-tier extraction: mint workflow entities from recurring episode clusters."""
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import combinations

MAX_WORKFLOW_EPISODES = 50
# Episode cap alone can't bound runtime when nothing clusters
# (C(50,25) ≈ 1e14); hard-cap combinations actually examined.
_MAX_COMBINATIONS = 200_000


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _in_window(created: str, now: datetime, window_days: int) -> bool:
    dt = _parse_iso(created)
    return bool(dt and (now - dt) <= timedelta(days=window_days))


def _episode_keys(entities: dict, now: datetime, window_days: int):
    for name, info in entities.items():
        if info.get("entityType") != "episode":
            continue
        if not name.startswith("episode:churn:"):
            continue
        if not _in_window(info.get("_created", ""), now, window_days):
            continue
        obs = set(info.get("observations") or [])
        neigh = set(info.get("_neighbors") or [])
        yield name, obs, neigh


def extract_workflows(
    entities: dict,
    now_iso: str,
    window_days: int = 30,
    min_episodes: int = 3,
    min_shared_obs: int = 2,
    min_shared_neighbors: int = 2,
) -> tuple[list[dict], list[dict]]:
    """Greedy: pick the largest cluster whose members share enough obs+neighbors."""
    now = _parse_iso(now_iso) or datetime.now(timezone.utc)
    eps = list(_episode_keys(entities, now, window_days))
    if len(eps) < min_episodes:
        return [], []

    # why: combinations is O(2^N); cap to keep maintenance latency bounded.
    if len(eps) > MAX_WORKFLOW_EPISODES:
        eps = sorted(eps, key=lambda e: e[0], reverse=True)[:MAX_WORKFLOW_EPISODES]

    used: set[str] = set()
    workflows: list[dict] = []
    relations: list[dict] = []

    eps.sort(key=lambda e: e[0], reverse=True)

    examined = 0
    for r in range(len(eps), min_episodes - 1, -1):
        if examined > _MAX_COMBINATIONS:
            break
        for combo in combinations(eps, r):
            examined += 1
            if examined > _MAX_COMBINATIONS:
                break
            if any(name in used for name, _, _ in combo):
                continue
            shared_obs = set.intersection(*(o for _, o, _ in combo))
            shared_neigh = set.intersection(*(n for _, _, n in combo))
            if len(shared_obs) < min_shared_obs:
                continue
            if len(shared_neigh) < min_shared_neighbors:
                continue
            members = [name for name, _, _ in combo]
            key = '+'.join(sorted(shared_neigh))
            # why: include members so disjoint clusters that share an
            # identical neighbor set don't collide on one workflow name.
            digest = hashlib.sha256(
                (key + "|" + "+".join(sorted(members))).encode("utf-8")
            ).hexdigest()[:8]
            wf_name = f"workflow:{key[:64]}#{digest}"
            workflows.append({
                "name": wf_name,
                "entityType": "workflow",
                "observations": [
                    f"co-changed: {p}" for p in sorted(shared_obs)
                ] + [f"derived from {len(members)} episodes"],
                "_created": now_iso,
                "_source": "workflow:extractor",
            })
            for m in members:
                relations.append({
                    "from": wf_name, "to": m,
                    "relationType": "derived-from",
                })
                used.add(m)
            break
    return workflows, relations
