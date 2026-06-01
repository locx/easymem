import semantic_server.recall as recall


def test_stat_failure_does_not_consume_check_interval(tmp_path, monkeypatch):
    recall.init_recall_state(str(tmp_path))
    monkeypatch.setattr(recall, "_last_recall_check", 0.0, raising=False)
    monkeypatch.setattr(recall.time, "monotonic", lambda: 1000.0)

    def _boom(_path):
        raise OSError()

    monkeypatch.setattr(recall.os, "stat", _boom)
    recall.maybe_reload_recall_counts()

    # why: a transient stat failure must retry next tick, not skip the interval.
    assert recall._last_recall_check == 0.0
