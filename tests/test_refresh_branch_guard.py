import semantic_server.config as config


def test_refresh_branch_before_init_returns_unknown(monkeypatch):
    monkeypatch.setattr(config, "_project_dir", "")
    monkeypatch.setattr(config, "_current_branch", "")
    monkeypatch.setattr(config, "_branch_check_mono", 0.0, raising=False)

    branch, changed = config.refresh_branch()

    # why: uninitialized -> must not read .git/HEAD relative to cwd.
    assert branch == "unknown"
    assert changed is False
