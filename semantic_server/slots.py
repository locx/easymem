"""Pinned slots: persona / preferences / guidelines, injected at session start."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

SLOT_KEYS: tuple[str, ...] = ("persona", "preferences", "guidelines")


def _slots_path(memory_dir: str) -> Path:
    return Path(memory_dir) / "slots.json"


@contextmanager
def _slot_lock(memory_dir: str):
    # why: serialize read-modify-write so concurrent set_slot calls in
    # different processes can't lose each other's updates.
    if _fcntl is None:
        yield
        return
    Path(memory_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(memory_dir) / ".slots.lock", "a") as lf:
        _fcntl.flock(lf.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            _fcntl.flock(lf.fileno(), _fcntl.LOCK_UN)


def _read(memory_dir: str) -> dict[str, str]:
    path = _slots_path(memory_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    out: dict[str, str] = {}
    for k in SLOT_KEYS:
        v = raw.get(k, "")
        out[k] = v if isinstance(v, str) else ""
    return out


def _write(memory_dir: str, data: dict[str, str]) -> None:
    path = _slots_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.new")
    # why: fsync + os.replace mirrors aliases.json writes for crash-safety
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def get_slot(memory_dir: str, key: str) -> str:
    if key not in SLOT_KEYS:
        raise ValueError(f"unknown slot key: {key!r}")
    return _read(memory_dir).get(key, "")


def set_slot(memory_dir: str, key: str, value: str) -> None:
    if key not in SLOT_KEYS:
        raise ValueError(f"unknown slot key: {key!r}")
    with _slot_lock(memory_dir):
        data = _read(memory_dir)
        data[key] = value
        _write(memory_dir, data)


def list_slots(memory_dir: str) -> dict[str, str]:
    return _read(memory_dir)
