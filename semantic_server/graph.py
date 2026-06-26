"""Graph I/O: JSONL parsing, loading, locking, appending, rewriting."""
import json
import os
import sys
import time

from ._json import loads as _fast_loads
from ._json import dumps as _fast_dumps
from ._json import load as _fast_load

try:
    import fcntl
except ImportError:
    fcntl = None  # Windows — locking disabled

_lock_warned = False

from .config import (
    MAX_ENTITY_COUNT,
    MAX_GRAPH_BYTES,
    MAX_INPUT_CHARS,
    MAX_CACHED_OBS,
    GRAPH_LOCK_TIMEOUT,
    PARSE_TIME_BUDGET,
    normalize_iso_ts as _norm_ts,
)
from .cache import (
    index_cache,
    entity_cache,
    relation_cache,
    adjacency_cache,
    clear_index_cache,
    clear_entity_cache,
    clear_relation_cache,
    estimate_size,
    maybe_evict_caches,
)
from .io_utils import iter_jsonl
from .text import normalize_type


def _obs_dedup_key(obs):
    """Normalize an observation to a hashable dedup key."""
    if isinstance(obs, str):
        return ("s", obs)
    return ("d", json.dumps(obs, sort_keys=True))


def _merge_obs(prev_obs, new_obs, seen=None, cap=MAX_CACHED_OBS):
    """Merge new_obs into prev_obs with dedup + truncate.

    Returns the updated seen-set; caller may cache for reuse.
    cap=None disables truncation (full-fidelity loads for rewrites).
    """
    if seen is None:
        seen = {_obs_dedup_key(o) for o in prev_obs}
    for o in new_obs:
        k = _obs_dedup_key(o)
        if k not in seen:
            prev_obs.append(o)
            seen.add(k)
    if cap is not None and len(prev_obs) > cap:
        prev_obs[:] = prev_obs[-cap:]
        seen = {_obs_dedup_key(o) for o in prev_obs}
    return seen


def _merge_ts(prev, created, updated):
    """Keep earliest _created, latest _updated."""
    if created and (
        not prev.get("_created")
        or created < prev["_created"]
    ):
        prev["_created"] = created
    if updated and (
        not prev.get("_updated")
        or updated > prev["_updated"]
    ):
        prev["_updated"] = updated


def get_graph_mtime(memory_dir):
    """Get graph.jsonl (mtime, size) tuple, or None if missing."""
    graph_path = os.path.join(memory_dir, "graph.jsonl")
    try:
        st = os.stat(graph_path)
        return graph_path, (st.st_mtime, st.st_size)
    except OSError:
        return graph_path, None


_MAX_RAW_LINE_BYTES = MAX_INPUT_CHARS * 4


def _iter_graph_lines(f, start_offset, max_incr_bytes, deadline, aborted):
    line_count = 0
    for raw in f:
        end_offset = f.tell()
        if (end_offset - start_offset > max_incr_bytes):
            sys.stderr.write("warn: incremental read byte budget exceeded\n")
            aborted[0] = True
            break
        if len(raw) > _MAX_RAW_LINE_BYTES:
            continue
        line = raw.decode("utf-8", errors="replace")
        if "\ufffd" in line:
            sys.stderr.write(
                f"warn: skipped invalid UTF-8 at offset {f.tell()}\n"
            )
            continue
        line = line.strip()
        if not line:
            continue
        line_count += 1
        if line_count % 1000 == 0 and time.monotonic() > deadline:
            sys.stderr.write(f"warn: parse time budget exceeded after {line_count} lines\n")
            aborted[0] = True
            break
        yield line, end_offset


def _handle_entity_entry(entities, obs_keys, obj):
    """Parse one entity JSONL entry.

    obs_keys is a sidecar {name: set(dedup_keys)} used for fast
    dedup-merge across appends. Kept out of the entity dict so the
    data model stays clean.
    """
    name = obj.get("name", "")
    if isinstance(name, str):
        name = name.strip()
    if not name:
        return
    if name not in entities and len(entities) >= MAX_ENTITY_COUNT:
        # why: surface truncation so callers about to rewrite the graph can
        # bail out instead of persisting the truncated snapshot to disk.
        entity_cache["truncated"] = True
        return
    obs = obj.get("observations", [])
    if not isinstance(obs, list):
        obs = []
    if name in entities:
        prev = entities[name]
        obs_keys[name] = _merge_obs(
            prev.get("observations", []),
            obs,
            obs_keys.get(name),
        )
        _merge_ts(
            prev,
            _norm_ts(obj.get("_created", "")),
            _norm_ts(obj.get("_updated", "")),
        )
        branch = obj.get("_branch")
        if branch and not prev.get("_branch"):
            prev["_branch"] = branch
        source = obj.get("_source")
        if source and not prev.get("_source"):
            prev["_source"] = source
    else:
        obs_list = list(obs)
        if len(obs_list) > MAX_CACHED_OBS:
            obs_list = obs_list[-MAX_CACHED_OBS:]
        info = {
            "entityType": normalize_type(obj.get("entityType", "")),
            "observations": obs_list,
            "_created": _norm_ts(obj.get("_created", "")),
            "_updated": _norm_ts(obj.get("_updated", "")),
        }
        branch = obj.get("_branch")
        if branch:
            info["_branch"] = branch
        # why: keep _source so rewrites don't strip provenance and session
        # diversification can read it from cache instead of scanning the file.
        source = obj.get("_source")
        if source:
            info["_source"] = source
        entities[name] = info
        obs_keys[name] = {_obs_dedup_key(o) for o in obs_list}


def _handle_relation_entry(relations, rel_seen, obj):
    r_from = obj.get("from", "")
    r_to = obj.get("to", "")
    if not isinstance(r_from, str) or not isinstance(r_to, str):
        return
    if not r_from or not r_to:
        return
    r_type = normalize_type(obj.get("relationType", ""))
    rel_key = (r_from, r_to, r_type)
    if rel_key not in rel_seen:
        rel_seen.add(rel_key)
        relations.append({
            "from": r_from,
            "to": r_to,
            "relationType": r_type,
        })

def _parse_graph_file(graph_path, start_offset=0, seed_obs_keys=None):
    """Parse graph.jsonl into (entities_dict, relations_list, obs_keys, end_offset).

    seed_obs_keys lets the incremental path reuse a prior dedup set,
    avoiding O(M) rebuilds per entity.
    """
    entities = {}
    relations = []
    rel_seen = set()
    obs_keys = dict(seed_obs_keys) if seed_obs_keys else {}
    deadline = time.monotonic() + PARSE_TIME_BUDGET
    end_offset = start_offset
    aborted = [False]
    max_incr_bytes = MAX_GRAPH_BYTES if start_offset == 0 \
        else min(MAX_GRAPH_BYTES, 10_000_000)
    try:
        with open(graph_path, "rb") as f:
            if start_offset > 0:
                f.seek(start_offset)
            for line, offset in _iter_graph_lines(
                f, start_offset, max_incr_bytes, deadline, aborted,
            ):
                end_offset = offset
                try:
                    obj = _fast_loads(line)
                    if not isinstance(obj, dict):
                        continue
                    t = obj.get("type")
                    if t == "entity":
                        _handle_entity_entry(entities, obs_keys, obj)
                    elif t == "relation":
                        _handle_relation_entry(relations, rel_seen, obj)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return None, None, None, 0, False
    # Trim obs_keys to entities actually present
    for name in list(obs_keys):
        if name not in entities:
            obs_keys.pop(name, None)
    return entities, relations, obs_keys, end_offset, aborted[0]


def _do_full_parse(graph_path, mtime):
    """Full graph parse — populates both caches."""
    # why: reset stale truncated flag from a prior parse; _handle_entity_entry
    # will set it back to True if this parse also hits the cap.
    entity_cache["truncated"] = False
    entities, relations, obs_keys, offset, aborted = \
        _parse_graph_file(graph_path)
    if entities is None:
        clear_entity_cache()
        clear_relation_cache()
        return {}, []

    entity_cache["data"] = entities
    # why: a budget-aborted parse is incomplete — mark truncated so a rewrite
    # can't persist the partial set, and store no matching mtime so the next
    # load retries instead of serving the partial set as authoritative.
    entity_cache["truncated"] = aborted or entity_cache.get("truncated", False)
    entity_cache["mtime"] = None if aborted else mtime
    entity_cache["path"] = graph_path
    entity_cache["size"] = estimate_size(entities)
    entity_cache["offset"] = offset
    # why: incremental reads must confirm they continue the same file; a
    # cross-process rewrite (os.replace) changes the inode.
    try:
        entity_cache["ino"] = os.stat(graph_path).st_ino
    except OSError:
        entity_cache["ino"] = None
    entity_cache["append_only"] = False
    entity_cache["obs_keys"] = obs_keys
    entity_cache["obs_keys_size"] = None
    entity_cache.pop("_pre_invalidate_mtime", None)
    relation_cache["data"] = relations
    # why: like the entity side, an aborted parse yields partial relations —
    # store no matching mtime so the next relation load re-parses.
    relation_cache["mtime"] = None if aborted else mtime
    relation_cache["path"] = graph_path
    relation_cache["size"] = estimate_size(relations)
    maybe_evict_caches()
    return entities, relations


def load_index(memory_dir):
    """Load TF-IDF index with mtime-based caching."""
    index_path = os.path.join(memory_dir, "tfidf_index.json")

    try:
        mtime = os.path.getmtime(index_path)
    except OSError:
        clear_index_cache()
        return None

    if (index_cache["data"] is not None
            and index_cache["path"] == index_path
            and index_cache["mtime"] == mtime):
        return index_cache["data"]

    try:
        with open(index_path, encoding="utf-8") as f:
            data = _fast_load(f)
        # why: size by real on-disk bytes — estimate_size over-prices the
        # index ~3.5x and evicts it from the cap on every load (thrash).
        size = os.path.getsize(index_path)
        index_cache["data"] = data
        index_cache["mtime"] = mtime
        index_cache["path"] = index_path
        index_cache["size"] = size
        maybe_evict_caches()
        return data
    except (json.JSONDecodeError, ValueError, OSError):
        clear_index_cache()
        return None


def _merge_incremental_data(existing_ents, existing_obs_keys,
                            new_ents, new_obs_keys, new_rels):
    """Merge incremental parse results into cache, reusing dedup sets."""
    for name, info in new_ents.items():
        if name in existing_ents:
            # why: copy before mutating so a reader holding the pre-merge
            # dict never sees a half-updated entity.
            prev = dict(existing_ents[name])
            prev["observations"] = list(prev.get("observations", []))
            seen = existing_obs_keys.get(name)
            existing_obs_keys[name] = _merge_obs(
                prev["observations"],
                info.get("observations", []),
                seen,
            )
            _merge_ts(prev, info.get("_created", ""), info.get("_updated", ""))
            existing_ents[name] = prev
        else:
            if len(existing_ents) >= MAX_ENTITY_COUNT:
                # why: enforce the cap on the merged total, not just the
                # delta, so incremental reads can't grow past it silently.
                entity_cache["truncated"] = True
                continue
            existing_ents[name] = info
            # Reuse obs_keys produced by parser when available
            existing_obs_keys[name] = (
                new_obs_keys.get(name)
                or {_obs_dedup_key(o) for o in info.get("observations", [])}
            )

    if relation_cache["data"] is not None and new_rels:
        existing_keys = {
            (r["from"], r["to"], r.get("relationType", ""))
            for r in relation_cache["data"]
        }
        added = [
            r for r in new_rels
            if (r["from"], r["to"], r.get("relationType", "")) not in existing_keys
        ]
        if added:
            relation_cache["data"].extend(added)
            # why: add only per-element bytes; estimate_size(list) re-adds the
            # container overhead each merge, inflating the accounted size.
            relation_cache["size"] += sum(estimate_size(r) for r in added)


def _try_incremental_load(graph_path, mtime, prev_offset):
    """Attempt incremental load from prev_offset. Returns entities or None on failure."""
    # Size regression guard: if file shrank, it was rewritten — force full load
    current_size = mtime[1] if isinstance(mtime, tuple) else None
    if current_size is not None and current_size < prev_offset:
        return None

    # why: a same/greater-size cross-process rewrite changes the inode;
    # without this the delta read would continue a different file.
    try:
        if os.stat(graph_path).st_ino != entity_cache.get("ino"):
            return None
    except OSError:
        return None

    if mtime == entity_cache.get("_pre_invalidate_mtime", 0.0):
        entity_cache["append_only"] = False
        return None

    existing_obs_keys = entity_cache.get("obs_keys") or {}
    new_ents, new_rels, new_obs_keys, offset, aborted = _parse_graph_file(
        graph_path, start_offset=prev_offset,
    )
    if new_ents is None:
        entity_cache["append_only"] = False
        entity_cache.pop("_pre_invalidate_mtime", None)
        return None

    # why: merge into a copy and swap so a concurrent reader holding the
    # prior dict never observes a half-merged state.
    existing = dict(entity_cache["data"])
    # why: clear a stale truncation flag from a prior capped full parse;
    # the merge re-sets it if the merged total is still over cap.
    entity_cache["truncated"] = False
    _merge_incremental_data(
        existing, existing_obs_keys,
        new_ents, new_obs_keys or {}, new_rels,
    )
    entity_cache["data"] = existing
    # why: a budget-aborted delta is incomplete — don't stamp a matching
    # mtime (force re-read) and block rewrites until a full parse completes.
    if aborted:
        entity_cache["truncated"] = True
    entity_cache["mtime"] = None if aborted else mtime
    entity_cache["offset"] = offset
    entity_cache["size"] = estimate_size(existing)
    entity_cache["append_only"] = False
    entity_cache["obs_keys"] = existing_obs_keys
    entity_cache["obs_keys_size"] = None
    entity_cache.pop("_pre_invalidate_mtime", None)
    if relation_cache["data"] is not None:
        relation_cache["mtime"] = mtime
    if new_rels:
        # why: zero size too, else _cache_total keeps counting phantom
        # bytes until the next traversal rebuild and may over-evict.
        adjacency_cache.update(
            outbound=None, inbound=None, mtime=0.0, size=0
        )
    elif adjacency_cache.get("outbound") is not None:
        # why: relations unchanged — keep adjacency valid for the new mtime
        # so the next traversal doesn't needlessly rebuild it.
        adjacency_cache["mtime"] = mtime
    maybe_evict_caches()
    return existing


def _full_load(graph_path, mtime):
    """Full parse path."""
    entities, _ = _do_full_parse(graph_path, mtime)
    return entities


def load_graph_entities(memory_dir):
    """Load entities with mtime cache + incremental reads."""
    graph_path, mtime = get_graph_mtime(memory_dir)
    if mtime is None:
        clear_entity_cache()
        clear_relation_cache()
        return {}

    if (entity_cache["data"] is not None
            and entity_cache["path"] == graph_path
            and entity_cache["mtime"] == mtime):
        return entity_cache["data"]

    prev_offset = entity_cache.get("offset", 0)
    if (entity_cache.get("append_only")
            and entity_cache["data"] is not None
            and entity_cache["path"] == graph_path
            and prev_offset > 0):
        result = _try_incremental_load(graph_path, mtime, prev_offset)
        if result is not None:
            return result

    return _full_load(graph_path, mtime)


def load_graph_relations(memory_dir):
    """Load relations with mtime-based caching."""
    graph_path, mtime = get_graph_mtime(memory_dir)
    if mtime is None:
        clear_entity_cache()
        clear_relation_cache()
        return []

    if (relation_cache["data"] is not None
            and relation_cache["path"] == graph_path
            and relation_cache["mtime"] == mtime):
        return relation_cache["data"]

    _, relations = _do_full_parse(graph_path, mtime)
    return relations


# keys with dedicated merge handling in load_graph_full
_ENTITY_MERGED_KEYS = (
    "type", "name", "entityType", "observations", "_created", "_updated",
)


def load_graph_full(memory_dir):
    """Merged, uncapped graph view: (entities, relations, others).

    why: cached loads truncate observations (MAX_CACHED_OBS) for memory
    bounds; a rewrite fed from that view would durably drop data. This
    loader merges delta lines with no cap and preserves unknown fields.
    """
    graph_path = os.path.join(memory_dir, "graph.jsonl")
    entities = {}
    obs_keys = {}
    relations = []
    rel_seen = set()
    others = []
    # Load-time dedup (entity name, relation from/to/type) is the backstop that
    # collapses merge_pending fsync-failure re-appends; never weaken it.
    for obj in iter_jsonl(graph_path):
        t = obj.get("type")
        if t == "entity":
            name = obj.get("name", "")
            if isinstance(name, str):
                name = name.strip()
            if not name:
                continue
            obs = obj.get("observations", [])
            if not isinstance(obs, list):
                obs = []
            if name in entities:
                prev = entities[name]
                obs_keys[name] = _merge_obs(
                    prev["observations"], obs,
                    obs_keys.get(name), cap=None,
                )
                _merge_ts(
                    prev,
                    _norm_ts(obj.get("_created", "")),
                    _norm_ts(obj.get("_updated", "")),
                )
                for k, v in obj.items():
                    if k in _ENTITY_MERGED_KEYS:
                        continue
                    if v and not prev.get(k):
                        prev[k] = v
            else:
                info = {
                    k: v for k, v in obj.items()
                    if k not in ("type", "name")
                }
                info["entityType"] = normalize_type(
                    obj.get("entityType", ""))
                info["observations"] = list(obs)
                info["_created"] = _norm_ts(obj.get("_created", ""))
                info["_updated"] = _norm_ts(obj.get("_updated", ""))
                entities[name] = info
                obs_keys[name] = {_obs_dedup_key(o) for o in obs}
        elif t == "relation":
            r_from = obj.get("from", "")
            r_to = obj.get("to", "")
            if not isinstance(r_from, str) or not isinstance(r_to, str):
                continue
            if not r_from or not r_to:
                continue
            rel = {k: v for k, v in obj.items() if k != "type"}
            rel["relationType"] = normalize_type(
                obj.get("relationType", ""))
            key = (r_from, r_to, rel["relationType"])
            if key not in rel_seen:
                rel_seen.add(key)
                relations.append(rel)
        else:
            others.append(obj)
    return entities, relations, others


# --- Write infrastructure ---


class GraphLock:
    """Exclusive graph file lock with timeout."""
    __slots__ = ("_fd", "_path", "acquired", "_timeout")

    def __init__(self, memory_dir, timeout=None):
        self._path = os.path.join(
            memory_dir, ".graph.lock"
        )
        self._fd = None
        self.acquired = False
        self._timeout = timeout

    def __enter__(self):
        if fcntl is None:
            global _lock_warned
            if not _lock_warned:
                # why: no OS lock here; warn once so unguarded concurrent
                # writes aren't silent (single-process use still works).
                sys.stderr.write(
                    "warn: graph lock unavailable; writes unguarded\n"
                )
                _lock_warned = True
            self.acquired = True
            return self
        try:
            self._fd = open(self._path, "a")
        except OSError as exc:
            sys.stderr.write(f"warn: graph lock open failed: {exc}\n")
            return self
        # try/finally so signals (KeyboardInterrupt etc.) don't leak the fd.
        try:
            delay = 0.01
            timeout = (GRAPH_LOCK_TIMEOUT if self._timeout is None
                       else self._timeout)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(
                        self._fd,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    self.acquired = True
                    return self
                except (IOError, OSError):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(delay)
                    delay = min(delay * 2, 0.5)
            # why: a zero-timeout try-once skip is expected under contention;
            # only warn when a real wait actually elapsed.
            if timeout > 0:
                sys.stderr.write(
                    "warn: graph lock timeout after "
                    f"{timeout}s\n"
                )
        finally:
            if not self.acquired and self._fd is not None:
                self._fd.close()
                self._fd = None
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                if self.acquired and fcntl is not None:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            self._fd.close()
            self._fd = None
            self.acquired = False
        return False


def invalidate_caches():
    """Invalidate all in-process caches after a write."""
    clear_entity_cache()
    clear_relation_cache()
    clear_index_cache()


def invalidate_entity_cache_only():
    """Mark entity cache for incremental reload."""
    if entity_cache["data"] is not None:
        # Record mtime before invalidation for coherence guard
        entity_cache["_pre_invalidate_mtime"] = \
            entity_cache.get("mtime", 0.0)
        entity_cache["append_only"] = True
        entity_cache["mtime"] = 0.0
    else:
        clear_entity_cache()


def invalidate_relation_cache_only():
    """Invalidate relation cache (adjacency handled separately)."""
    clear_relation_cache()


def check_graph_size(memory_dir):
    """Guard against writes to oversized graphs."""
    graph_path = os.path.join(memory_dir, "graph.jsonl")
    try:
        size = os.path.getsize(graph_path)
        if size > MAX_GRAPH_BYTES:
            return {
                "error": f"Graph too large ({size} bytes, "
                         f"max {MAX_GRAPH_BYTES}). Run "
                         f"maintenance to prune first."
            }
    except OSError:
        pass
    return None


def append_jsonl(memory_dir, entries, do_fsync=True):
    """Append JSONL lines under lock; fsync inside the locked block."""
    graph_path = os.path.join(memory_dir, "graph.jsonl")
    lines = []
    with GraphLock(memory_dir) as lock:
        if not lock.acquired:
            return False
        for e in entries:
            try:
                lines.append(_fast_dumps(e) + "\n")
            except (TypeError, ValueError, OverflowError):
                continue
        if not lines:
            return True
        with open(graph_path, "a", encoding="utf-8") as f:
            f.writelines(lines)
            f.flush()
            if do_fsync:
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
    return True


def _sweep_orphan_temps(graph_path):
    # why: a .new.<pid> temp is created before its writer acquires the lock,
    # so unlinking under our lock could delete a live writer's temp. Only
    # unlink when <pid> is provably dead; skip if alive or unparseable.
    prefix = os.path.basename(graph_path) + ".new."
    directory = os.path.dirname(graph_path) or "."
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        pid_str = rest.split(".", 1)[0]
        if not pid_str.isdigit():
            continue
        try:
            os.kill(int(pid_str), 0)
            continue  # pid alive — not an orphan
        except ProcessLookupError:
            pass  # pid dead — safe to reclaim
        except (OSError, OverflowError):
            # EPERM (alive, not ours) or pid out of C-long range — never delete
            continue
        try:
            os.unlink(os.path.join(directory, name))
        except OSError:
            pass


def _build_rewrite_line(entry_type, name_or_r, info_or_none):
    """Build one JSONL line for rewrite_graph."""
    if entry_type == "entity":
        if not name_or_r or not isinstance(name_or_r, str):
            return None
        entry = {"type": "entity", "name": name_or_r}
        entry.update(info_or_none)
    else:
        if not info_or_none.get("from") or not info_or_none.get("to"):
            return None
        entry = {"type": "relation"}
        entry.update(info_or_none)
    try:
        return _fast_dumps(entry) + "\n"
    except (TypeError, ValueError, OverflowError):
        return None


def rewrite_graph(memory_dir, entities_dict, relations, *, others=None,
                  _lock_held=False, _full_source=False):
    """Atomic rewrite: temp file + fsync + os.replace.

    _lock_held: caller already holds GraphLock — skip the inner acquire to
    avoid fcntl.flock self-conflict across two fds in the same process.

    Refuses to write when entity_cache marks the loaded set as truncated;
    persisting a truncated snapshot would permanently drop entities past
    cap. _full_source=True asserts the input came from load_graph_full
    (uncapped), so the cache's truncation state is irrelevant.
    """
    if entity_cache.get("truncated") and not _full_source:
        raise OSError("refusing to rewrite from a truncated entity cache")
    graph_path = os.path.join(memory_dir, "graph.jsonl")
    # why: per-writer temp so concurrent rewriters can't clobber a shared
    # .new file before either acquires the lock.
    tmp = f"{graph_path}.new.{os.getpid()}.{time.monotonic_ns()}"
    dropped = 0

    def _lines():
        nonlocal dropped
        for name, info in entities_dict.items():
            line = _build_rewrite_line("entity", name, info)
            if line is None:
                dropped += 1
                continue
            yield line
        for r in relations:
            line = _build_rewrite_line("relation", None, r)
            if line is None:
                dropped += 1
                continue
            yield line
        # why: non-entity/relation rows must survive a rewrite, not be dropped.
        for o in (others or []):
            try:
                yield _fast_dumps(o) + "\n"
            except (TypeError, ValueError, OverflowError):
                dropped += 1

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(_lines())
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    if dropped > 0:
        sys.stderr.write(f"warn: rewrite_graph dropped {dropped} invalid lines\n")

    def _commit():
        try:
            os.replace(tmp, graph_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        # why: replace consumed our temp; reclaim only .new.* temps whose
        # owning pid is dead (a live writer's temp must survive).
        _sweep_orphan_temps(graph_path)
        # Rewrite touches entity/relation data but never tfidf_index.json;
        # keep the on-disk index cache hot to avoid a costly reload.
        clear_entity_cache()
        clear_relation_cache()

    if _lock_held:
        _commit()
        return

    with GraphLock(memory_dir) as lock:
        if not lock.acquired:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise OSError("Graph lock timeout")
        _commit()


def rewrite_graph_locked(memory_dir, transform):
    """Load full graph, apply transform, rewrite — under one lock.

    why: mtime guards taken outside the lock can still lose an append
    that lands between check and replace; holding the lock across
    load+transform+replace closes that window. transform(entities,
    relations) returns (entities, relations) or None to abort unwritten.
    """
    with GraphLock(memory_dir) as lock:
        if not lock.acquired:
            raise OSError("Graph lock timeout")
        entities, relations, others = load_graph_full(memory_dir)
        result = transform(entities, relations)
        if result is None:
            return False
        new_entities, new_relations = result
        rewrite_graph(
            memory_dir, new_entities, new_relations,
            others=others, _lock_held=True, _full_source=True,
        )
    return True
