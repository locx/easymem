import maintenance


def _ents():
    return [{
        "name": "Widget", "entityType": "component",
        "observations": ["alpha beta gamma"], "_branch": "",
    }]


def test_concurrently_set_dirty_marker_survives_rebuild(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    # Snapshot saw no marker; a writer marks the index stale afterward.
    token = maintenance._dirty_marker_token(str(mem))
    assert token is None
    dirty = mem / ".index-dirty"
    dirty.write_text("")

    maintenance.build_tfidf_index(_ents(), str(mem), token)
    assert dirty.exists()


def test_unchanged_dirty_marker_is_cleared(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    dirty = mem / ".index-dirty"
    dirty.write_text("")
    token = maintenance._dirty_marker_token(str(mem))

    maintenance.build_tfidf_index(_ents(), str(mem), token)
    assert not dirty.exists()
