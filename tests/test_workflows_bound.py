import time

from semantic_server import workflows
from semantic_server.workflows import extract_workflows


def _ep(name, files, neighbors, created):
    return name, {
        "entityType": "episode",
        "observations": files,
        "_created": created,
        "_neighbors": neighbors,
    }


def test_extract_workflows_bounded_when_nothing_clusters(monkeypatch):
    monkeypatch.setattr(workflows, "_MAX_COMBINATIONS", 5000)
    # Each episode has a unique neighbor → no combo ever meets the
    # neighbor threshold, so the old code enumerated C(n, r) exhaustively.
    eps = dict(
        _ep(f"episode:churn:{i}", ["src/a.py", "src/b.py"],
            [f"N{i}"], "2026-05-15T00:00:00Z")
        for i in range(28)
    )
    start = time.monotonic()
    wfs, rels = extract_workflows(
        eps, now_iso="2026-05-20T00:00:00Z", window_days=30,
        min_episodes=3, min_shared_obs=2, min_shared_neighbors=2,
    )
    assert wfs == [] and rels == []
    assert time.monotonic() - start < 5.0


def test_disjoint_clusters_sharing_neighbors_get_distinct_names():
    eps = {}
    for i in range(4):
        eps.update(dict([_ep(
            f"episode:churn:a{i}", ["fA1", "fA2"],
            ["S1", "S2"], "2026-05-15T00:00:00Z")]))
    for i in range(3):
        eps.update(dict([_ep(
            f"episode:churn:b{i}", ["fB1", "fB2"],
            ["S1", "S2"], "2026-05-15T00:00:00Z")]))

    wfs, _ = extract_workflows(
        eps, now_iso="2026-05-20T00:00:00Z", window_days=30,
        min_episodes=3, min_shared_obs=2, min_shared_neighbors=2,
    )
    # The 4-member and 3-member clusters mint at r=4 and r=3; both share
    # the {S1,S2} neighbor set, so names must not collide.
    assert len(wfs) == 2
    assert wfs[0]["name"] != wfs[1]["name"]
