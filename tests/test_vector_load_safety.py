import numpy as np

from semantic_server import vector


def test_load_index_refuses_object_array_npz(tmp_path):
    # why: a crafted .npz with object arrays must never be pickle-deserialized.
    path = tmp_path / "vec_index.npz"
    np.savez(
        str(path),
        vecs=np.array([{"a": 1}], dtype=object),
        names=np.array(["x"]),
        model=np.array("m"),
        dim=np.array(1),
    )
    assert vector.load_index(str(tmp_path)) is None
