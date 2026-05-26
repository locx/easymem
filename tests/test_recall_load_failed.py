import json
import os

from semantic_server import recall


def _reset_module_state():
    recall.recall_counts.clear()
    recall.recall_dirty = False
    recall.recall_last_flush = 0.0
    recall.recall_path = ""
    recall.recall_mtime = 0.0
    recall._last_recall_check = 0.0
    recall._load_failed = False


def test_corrupt_counts_preserves_disk_history(tmp_path):
    # why: a transient JSON read failure must not let the next flush overwrite
    # disk with `{}` — that path previously destroyed history permanently.
    _reset_module_state()
    counts_path = tmp_path / "recall_counts.json"
    counts_path.write_text('{"AuthService": 5, "Logger": 2}', encoding="utf-8")
    recall.init_recall_state(str(tmp_path))
    assert recall.recall_counts == {"AuthService": 5, "Logger": 2}

    counts_path.write_text("{not valid json", encoding="utf-8")
    os.utime(counts_path, None)
    recall._last_recall_check = 0.0
    recall.maybe_reload_recall_counts()
    assert recall._load_failed is True

    recall.record_recalls(["NewlyTouched"])
    assert recall.recall_dirty is True
    recall.flush_recall_counts()

    # Disk content untouched while load_failed is true.
    on_disk = counts_path.read_text(encoding="utf-8")
    assert on_disk == "{not valid json"
    _reset_module_state()


def test_missing_counts_file_is_not_load_failed(tmp_path):
    # why: FileNotFoundError on first run is normal, not a degraded state —
    # flush must still be permitted to create the initial sidecar.
    _reset_module_state()
    recall.init_recall_state(str(tmp_path))
    assert recall._load_failed is False
    assert recall.recall_counts == {}

    recall.record_recalls(["Seed"])
    recall.flush_recall_counts()
    written = json.loads((tmp_path / "recall_counts.json").read_text())
    assert written == {"Seed": 1}
    _reset_module_state()
