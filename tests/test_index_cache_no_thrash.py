"""Index cache must survive eviction so warm search doesn't re-read+re-parse
the TF-IDF index every query (cache thrash)."""
import json
import os

from semantic_server import cache, graph, search
import maintenance


def _seed(memory_dir):
    graph.invalidate_caches()
    cache.clear_index_cache()
    entities = []
    lines = []
    for i in range(60):
        name = f"Entity{i}"
        obs = [f"observation about topic {i} login token service handler"]
        ent = {"name": name, "entityType": "service", "observations": obs}
        entities.append(ent)
        lines.append(json.dumps({"type": "entity", **ent}))
    with open(os.path.join(memory_dir, "graph.jsonl"), "w") as f:
        f.write("\n".join(lines) + "\n")
    maintenance.build_tfidf_index(entities, memory_dir)


def test_index_stays_cached_under_eviction_pressure(tmp_path, monkeypatch):
    md = str(tmp_path)
    _seed(md)

    # Warm the index cache via the real search entrypoint.
    search.search("login token handler", md, top_k=5)
    assert cache.index_cache["data"] is not None
    assert cache.index_cache["mtime"] != 0

    recorded = cache.index_cache["size"]
    on_disk = os.path.getsize(os.path.join(md, "tfidf_index.json"))
    # Conservative RAM figure must not under-count the on-disk bytes.
    assert recorded >= on_disk

    # Make the index the LARGEST cache and push total over the cap. Under the
    # old largest-first policy this dropped the index every query (thrash);
    # the fix evicts the rebuildable index last instead. Sibling sizes are
    # set so dropping them alone brings total under cap.
    monkeypatch.setattr(cache, "MAX_CACHE_BYTES", recorded + 10)
    cache.entity_cache["size"] = recorded // 2
    cache.relation_cache["size"] = recorded // 2

    cache.maybe_evict_caches()

    # The rebuildable index is evicted last; siblings absorb the cap.
    assert cache.index_cache["mtime"] != 0
    assert cache.index_cache["data"] is not None
    assert cache.entity_cache["size"] == 0

    # A subsequent search still serves the index from cache (no re-read).
    search.search("service token", md, top_k=5)
    assert cache.index_cache["data"] is not None
    assert cache.index_cache["mtime"] != 0
