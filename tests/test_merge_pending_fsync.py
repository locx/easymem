import os

from semantic_server.io_utils import merge_pending


def test_keeps_processing_on_fsync_failure(tmp_path, monkeypatch):
    graph = tmp_path / "graph.jsonl"
    graph.write_text("", encoding="utf-8")
    pending = tmp_path / "graph.jsonl.pending"
    pending.write_text('{"type": "entity", "name": "x"}\n', encoding="utf-8")

    def _boom(_fd):
        raise OSError()

    monkeypatch.setattr(os, "fsync", _boom)
    merge_pending(str(tmp_path), str(graph), str(pending), lock=None)

    processing = tmp_path / "graph.jsonl.pending.processing"
    # why: durability unconfirmed -> keep .processing so the next tick retries.
    assert processing.exists()
