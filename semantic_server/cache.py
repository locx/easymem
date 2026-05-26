"""Cache infrastructure: mtime-based caches with size-aware eviction."""

import sys
from sys import getsizeof

from .config import MAX_CACHE_BYTES

index_cache = {
    "data": None, "mtime": 0.0, "path": "", "size": 0,
}
entity_cache = {
    "data": None, "mtime": 0.0, "path": "", "size": 0,
    "offset": 0, "append_only": False,
    # why: the parser silently caps at MAX_ENTITY_COUNT; if a caller then
    # round-trips entities through rewrite_graph, the truncated set persists.
    "truncated": False,
}
relation_cache = {
    "data": None, "mtime": 0.0, "path": "", "size": 0,
}
adjacency_cache = {
    "outbound": None, "inbound": None,
    "mtime": 0.0, "size": 0,
}

last_index_check = 0.0

_DEPTH_CAP = 3
_STRING_OVERHEAD = sys.getsizeof("")


def clear_index_cache():
    index_cache.update(
        data=None, mtime=0.0, path="", size=0
    )


def clear_entity_cache():
    entity_cache.update(
        data=None, mtime=0.0, path="", size=0,
        offset=0, append_only=False, truncated=False,
    )
    entity_cache.pop("_pre_invalidate_mtime", None)
    entity_cache.pop("obs_keys_size", None)


def clear_relation_cache():
    relation_cache.update(
        data=None, mtime=0.0, path="", size=0
    )
    adjacency_cache.update(
        outbound=None, inbound=None, mtime=0.0, size=0,
    )


def estimate_size(obj, _depth=0):
    """Estimate byte size via shallow walk of strings/containers.

    Traverses up to _DEPTH_CAP levels deep to price string payloads
    accurately. Past the cap, falls back to sys.getsizeof for speed.
    """
    if obj is None:
        return 0
    if _depth >= _DEPTH_CAP:
        return getsizeof(obj)
    if isinstance(obj, str):
        return _STRING_OVERHEAD + len(obj)
    if isinstance(obj, (int, float, bool)):
        return getsizeof(obj)
    if isinstance(obj, dict):
        total = getsizeof(obj)
        for k, v in obj.items():
            total += estimate_size(k, _depth + 1)
            total += estimate_size(v, _depth + 1)
        return total
    if isinstance(obj, (list, tuple, set, frozenset)):
        total = getsizeof(obj)
        for item in obj:
            total += estimate_size(item, _depth + 1)
        return total
    return getsizeof(obj)


def _obs_keys_size():
    # Memoize: estimate_size walks every dedup-key tuple, ~O(N·M).
    # Bust by setting entity_cache["obs_keys_size"] = None on mutation.
    cached = entity_cache.get("obs_keys_size")
    if cached is not None:
        return cached
    obs_keys = entity_cache.get("obs_keys") or {}
    size = estimate_size(obs_keys) if obs_keys else 0
    entity_cache["obs_keys_size"] = size
    return size


def _cache_total():
    # obs_keys is a sidecar dedup set hung off entity_cache; its bytes
    # are real RAM but were missing from the eviction trigger.
    return (index_cache["size"] + entity_cache["size"]
            + relation_cache["size"] + adjacency_cache["size"]
            + _obs_keys_size())


def maybe_evict_caches():
    """Evict caches by size (largest first) until under cap."""
    if _cache_total() <= MAX_CACHE_BYTES:
        return
    evictable = [
        (index_cache, clear_index_cache),
        (adjacency_cache,
         lambda: adjacency_cache.update(
             outbound=None, inbound=None,
             mtime=0.0, size=0)),
        (entity_cache, clear_entity_cache),
        (relation_cache, clear_relation_cache),
    ]
    # entity_cache effective size includes obs_keys sidecar.
    evictable.sort(
        key=lambda x: (
            x[0]["size"]
            + (_obs_keys_size() if x[0] is entity_cache else 0)
        ),
        reverse=True,
    )
    for cache, clear_fn in evictable:
        if cache["size"] > 0:
            clear_fn()
            if _cache_total() <= MAX_CACHE_BYTES:
                return
