"""Freshness multiplier: off by default, decays with age when enabled."""
import time

from semantic_server import search


def test_freshness_is_noop_by_default():
    assert search._FRESHNESS_ALPHA == 0.0
    info = {"_updated": "2000-01-01T00:00:00Z"}
    assert search._freshness_mult(info, time.time()) == 1.0


def test_fresh_outranks_old_when_enabled(monkeypatch):
    monkeypatch.setattr(search, "_FRESHNESS_ALPHA", 0.5)
    monkeypatch.setattr(search, "_FRESHNESS_HALFLIFE_DAYS", 30.0)
    now = time.time()
    old = {"_updated": "2000-01-01T00:00:00Z"}
    fresh = {"_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime(now))}
    assert search._freshness_mult(fresh, now) > search._freshness_mult(old, now)
    # bounded: never more than (1 + alpha)
    assert search._freshness_mult(fresh, now) <= 1.5 + 1e-9


def test_missing_or_bad_stamp_is_neutral(monkeypatch):
    monkeypatch.setattr(search, "_FRESHNESS_ALPHA", 0.5)
    now = time.time()
    assert search._freshness_mult({}, now) == 1.0
    assert search._freshness_mult({"_updated": "not-a-date"}, now) == 1.0


def test_env_parse_falls_back_on_bad_value(monkeypatch):
    # a typo'd opt-in env value must not crash; it falls back to the default
    monkeypatch.setenv("EASYMEM_FRESHNESS_ALPHA", "0.1x")
    monkeypatch.setenv("EASYMEM_LEXICAL_BOOST_MAX_QTOKENS", "")
    assert search._env_float("EASYMEM_FRESHNESS_ALPHA", "0.0") == 0.0
    assert search._env_int("EASYMEM_LEXICAL_BOOST_MAX_QTOKENS", "4") == 4


def test_zero_halflife_is_neutral(monkeypatch):
    monkeypatch.setattr(search, "_FRESHNESS_ALPHA", 0.5)
    monkeypatch.setattr(search, "_FRESHNESS_HALFLIFE_DAYS", 0.0)
    import time
    assert search._freshness_mult({"_updated": "2099-01-01T00:00:00Z"},
                                  time.time()) == 1.0


def test_lexical_boost_off_by_default():
    assert search._LEXICAL_BOOST == 0.0
    assert search._lexical_weight("favorite rice") == 1.0


def test_lexical_boost_only_short_queries(monkeypatch):
    monkeypatch.setattr(search, "_LEXICAL_BOOST", 0.5)
    monkeypatch.setattr(search, "_LEXICAL_BOOST_MAX_QTOKENS", 4)
    assert search._lexical_weight("favorite rice") == 1.5
    assert search._lexical_weight(
        "what did we decide about the auth refactor last week") == 1.0
