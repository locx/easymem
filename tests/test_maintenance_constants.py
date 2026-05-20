import maintenance


def test_episode_decay_days():
    assert maintenance.EPISODE_DECAY_DAYS == 14


def test_episode_survival_recall():
    assert maintenance.EPISODE_SURVIVAL_RECALL == 2
