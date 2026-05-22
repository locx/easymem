import numpy as np
import pytest

from semantic_server import vector


@pytest.fixture
def tiny_index(tmp_path):
    entities = {
        "AuthService": {
            "entityType": "service",
            "observations": ["Handles login and JWT tokens"],
        },
        "SyncManager": {
            "entityType": "component",
            "observations": ["Uses LWW conflict resolution"],
        },
        "Logger": {
            "entityType": "utility",
            "observations": ["Writes structured JSON to stderr"],
        },
    }
    names, vecs = vector.embed_entities(entities)
    npz_path = tmp_path / "vec_index.npz"
    vector.save_index(str(npz_path), names, vecs, model_id="test/m@1")
    return tmp_path


def test_vector_search_returns_ranked_results(tiny_index):
    # VECTOR_MIN_SIM floor drops near-zero/negative cosines so only the
    # semantically relevant entity survives; SyncManager/Logger fall out.
    results = vector.vector_search(
        str(tiny_index), "login authentication", top_k=3,
    )
    assert len(results) >= 1
    assert results[0][0] == "AuthService"
    assert all(isinstance(r[1], float) for r in results)
    assert all(s > vector.VECTOR_MIN_SIM for _, s in results)


def test_vector_search_empty_returns_empty(tmp_path):
    results = vector.vector_search(str(tmp_path), "anything", top_k=5)
    assert results == []


def test_vector_search_missing_npz_returns_empty(tmp_path):
    results = vector.vector_search(str(tmp_path), "query", top_k=5)
    assert results == []


def test_save_load_roundtrip(tiny_index):
    loaded = vector.load_index(str(tiny_index))
    assert loaded is not None
    assert len(loaded["names"]) == 3
    assert loaded["vecs"].dtype == np.int8
    assert loaded["model"] == "test/m@1"
