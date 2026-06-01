from semantic_server.graph import GraphLock


def test_graphlock_logs_when_lockfile_open_fails(tmp_path, capsys):
    # why: a directory at the lock path makes open(path, "a") raise OSError.
    (tmp_path / ".graph.lock").mkdir()

    with GraphLock(str(tmp_path)) as lock:
        assert lock.acquired is False

    err = capsys.readouterr().err
    assert "lock" in err.lower()
