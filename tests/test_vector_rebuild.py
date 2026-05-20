import time

from semantic_server import vector


def test_rebuild_when_missing(tmp_path):
    entities = {"X": {"entityType": "t", "observations": ["abc"]}}
    rebuilt = vector.rebuild_if_stale(str(tmp_path), entities, time.time())
    assert rebuilt is True
    assert (tmp_path / "vec_index.npz").exists()


def test_skip_when_fresh(tmp_path):
    entities = {"X": {"entityType": "t", "observations": ["abc"]}}
    mtime = time.time()
    vector.rebuild_if_stale(str(tmp_path), entities, mtime)
    rebuilt = vector.rebuild_if_stale(str(tmp_path), entities, mtime)
    assert rebuilt is False


def test_rebuild_when_count_changes(tmp_path):
    e1 = {"X": {"entityType": "t", "observations": ["abc"]}}
    vector.rebuild_if_stale(str(tmp_path), e1, time.time())
    e2 = dict(e1, Y={"entityType": "t", "observations": ["def"]})
    rebuilt = vector.rebuild_if_stale(str(tmp_path), e2, time.time())
    assert rebuilt is True
