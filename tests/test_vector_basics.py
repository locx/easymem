import numpy as np

from semantic_server import vector


def test_load_model_lazy():
    m = vector.get_model()
    assert m is not None


def test_encode_text_returns_at_least_256_dim():
    m = vector.get_model()
    v = m.encode(["hello world"])
    assert v.shape[1] >= 256


def test_l2_norm_quantize_int8():
    arr = np.array([[3.0, 4.0, 0.0]], dtype=np.float32)
    q = vector.l2_quantize_int8(arr)
    assert q.dtype == np.int8
    assert q[0, 0] == 76
    assert q[0, 1] == 102
    assert q[0, 2] == 0


def test_quantize_zero_vector_returns_zeros():
    arr = np.array([[0.0, 0.0, 0.0]], dtype=np.float32)
    q = vector.l2_quantize_int8(arr)
    assert (q == 0).all()
