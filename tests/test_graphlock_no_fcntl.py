import semantic_server.graph as g


def test_graphlock_warns_once_when_no_lock_primitive(
    tmp_path, monkeypatch, capsys
):
    # why: simulate a platform without fcntl (e.g. Windows).
    monkeypatch.setattr(g, "fcntl", None)
    monkeypatch.setattr(g, "_lock_warned", False, raising=False)

    with g.GraphLock(str(tmp_path)) as lock:
        assert lock.acquired is True
    first = capsys.readouterr().err
    assert "lock" in first.lower() and "unguarded" in first.lower()

    with g.GraphLock(str(tmp_path)):
        pass
    assert capsys.readouterr().err == ""
