from semantic_server import config


def test_embed_model_default():
    assert config.EMBED_MODEL == "minishlab/potion-retrieval-32M"


def test_embed_dim_default():
    assert config.EMBED_DIM == 256


def test_rrf_k_default():
    assert config.RRF_K == 60
