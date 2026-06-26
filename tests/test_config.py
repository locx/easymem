from semantic_server import config


def test_embed_model_default():
    assert config.EMBED_MODEL == "minishlab/potion-retrieval-32M"


def test_read_git_head_resolves_worktree_gitdir(tmp_path):
    # Worktree-style .git FILE pointing at a gitdir whose HEAD names a branch.
    real_git = tmp_path / "real.git"
    real_git.mkdir()
    (real_git / "HEAD").write_text("ref: refs/heads/feature-x\n")
    project = tmp_path / "wt"
    project.mkdir()
    (project / ".git").write_text(f"gitdir: {real_git}\n")
    assert config._read_git_head(str(project)) == "feature-x"


def test_embed_dim_default():
    assert config.EMBED_DIM == 256


def test_rrf_k_default():
    assert config.RRF_K == 60
