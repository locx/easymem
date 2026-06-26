"""Recall tracking — Hebbian reinforcement for search results.

Tracks entity recall frequency to boost relevance scoring.
OrderedDict for O(1) LRU eviction. Thread-safe via lock.
"""
import json
import os
import threading
import time
from collections import OrderedDict

from .config import (
    MAX_RECALL_ENTRIES,
    RECALL_CHECK_INTERVAL,
)

recall_counts = OrderedDict()
recall_dirty = False
recall_last_flush = 0.0
recall_path = ""
recall_mtime = 0.0
_last_recall_check = 0.0
_recall_lock = threading.Lock()
# why: on transient read error, preserve in-memory counts and disable flush —
# resetting to {} then flushing would permanently wipe disk history.
_load_failed = False
# why: increments not yet flushed, re-applied after a cross-process reload so
# an authoritative file replace can't silently drop local recall counts.
_unflushed: dict = {}


def init_recall_state(memory_dir):
    """Load recall counts from sidecar file."""
    global recall_counts, recall_path, recall_mtime, _load_failed
    recall_path = os.path.join(
        memory_dir, "recall_counts.json"
    )
    _unflushed.clear()
    # Sweep orphaned .tmp from prior crash
    tmp_path = recall_path + ".tmp"
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
    with _recall_lock:
        try:
            recall_mtime = os.path.getmtime(recall_path)
            with open(recall_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                recall_counts = OrderedDict(
                    (k, v) for k, v in data.items()
                    if isinstance(k, str)
                    and isinstance(v, (int, float))
                )
            _load_failed = False
        except FileNotFoundError:
            recall_counts = OrderedDict()
            recall_mtime = 0.0
            _load_failed = False
        except (OSError, json.JSONDecodeError, ValueError):
            _load_failed = True


def maybe_reload_recall_counts():
    """Reload if file changed, using mtime+file-offset for last-writer-wins."""
    global recall_mtime, _last_recall_check, _load_failed
    if not recall_path:
        return
    now = time.monotonic()
    if now - _last_recall_check < RECALL_CHECK_INTERVAL:
        return
    # why: stat the guard mtime and reload under one lock so a concurrent
    # flush can't slip a different file between the check and the open.
    with _recall_lock:
        try:
            mtime = os.stat(recall_path).st_mtime
        except OSError:
            return
        _last_recall_check = now
        if mtime == recall_mtime:
            return
        try:
            with open(recall_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Last-writer-wins: file is authoritative when mtime advanced.
                # Completely replace in-memory counts with file contents so
                # pruning from another process can reduce counts here too.
                recall_counts.clear()
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, (int, float)):
                        recall_counts[k] = v
                # Re-apply increments the reloaded file snapshot predates.
                for k, dv in _unflushed.items():
                    recall_counts[k] = recall_counts.get(k, 0) + dv
            recall_mtime = mtime
            _load_failed = False
        except (OSError, json.JSONDecodeError, ValueError):
            _load_failed = True


def record_recalls(entity_names):
    """Increment recall counts (no I/O — flush is deferred)."""
    global recall_dirty
    with _recall_lock:
        for name in entity_names:
            recall_counts[name] = (
                recall_counts.get(name, 0) + 1
            )
            recall_counts.move_to_end(name)
            _unflushed[name] = _unflushed.get(name, 0) + 1
        while len(recall_counts) > MAX_RECALL_ENTRIES:
            evicted, _ = recall_counts.popitem(last=False)
            # why: bound _unflushed with recall_counts and stop an evicted
            # entry from being resurrected by a later cross-process reload.
            _unflushed.pop(evicted, None)
        recall_dirty = True


def flush_recall_counts():
    """Atomic write of recall counts to disk. fsync runs after lock release."""
    global recall_dirty, recall_last_flush, recall_mtime
    if not recall_path:
        with _recall_lock:
            recall_dirty = False
        return
    with _recall_lock:
        # why: read the degraded/dirty flags under the same lock that writes
        # them so the decision and the snapshot are consistent.
        if _load_failed or not recall_dirty:
            return
        recall_last_flush = time.monotonic()
        snapshot = dict(recall_counts)
        flushed = dict(_unflushed)
        recall_dirty = False

    tmp = recall_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, recall_path)
        try:
            new_mtime = os.path.getmtime(recall_path)
        except OSError:
            new_mtime = None
        with _recall_lock:
            if new_mtime is not None:
                recall_mtime = new_mtime
            # why: drop only the deltas this flush persisted; increments
            # recorded since the snapshot stay pending for the next flush.
            for k, v in flushed.items():
                rem = _unflushed.get(k, 0) - v
                if rem > 0:
                    _unflushed[k] = rem
                else:
                    _unflushed.pop(k, None)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        # why: write failed — re-arm so the next tick (and atexit) retries
        # instead of silently dropping the still-in-memory counts.
        with _recall_lock:
            recall_dirty = True
