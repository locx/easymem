import json
from pathlib import Path

import pytest

from bench.locomo import load_locomo
from bench.run import evaluate

# why: tests run the bench CLI as a subprocess; derive the repo root from this
# file so it works on CI checkouts, not just one machine's absolute path.
_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def locomo_fixture(tmp_path):
    data = [
        {
            "qa": [
                {"question": "What is Alice's hobby?",
                 "answer": "knitting",
                 "evidence": ["D1:2"],
                 "category": 1},
                {"question": "When did they meet?",
                 "answer": "Tuesday",
                 "evidence": ["D1:1", "D2:1"],
                 "category": 3},
            ],
            "conversation": {
                "session_1": [
                    {"speaker": "Alice", "dia_id": "D1:1",
                     "text": "We first met on Tuesday."},
                    {"speaker": "Alice", "dia_id": "D1:2",
                     "text": "My hobby is knitting."},
                ],
                "session_2": [
                    {"speaker": "Bob", "dia_id": "D2:1",
                     "text": "Yes Tuesday it was."},
                ],
            },
        },
        {
            "qa": [
                {"question": "Where does Carol live?",
                 "answer": "Berlin",
                 "evidence": ["D3:1"],
                 "category": 1},
            ],
            "conversation": {
                "session_1": [
                    {"speaker": "Carol", "dia_id": "D3:1",
                     "text": "I live in Berlin."},
                ],
            },
        },
    ]
    path = tmp_path / "locomo_fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_returns_entities_and_queries(locomo_fixture):
    corpus = load_locomo(str(locomo_fixture))
    assert set(corpus.keys()) == {"entities", "queries"}


def test_entity_per_turn(locomo_fixture):
    corpus = load_locomo(str(locomo_fixture))
    names = {e["name"] for e in corpus["entities"]}
    assert names == {"D1:1", "D1:2", "D2:1", "D3:1"}


def test_entity_observations_include_text_and_speaker(locomo_fixture):
    corpus = load_locomo(str(locomo_fixture))
    by_name = {e["name"]: e for e in corpus["entities"]}
    obs = by_name["D1:2"]["observations"]
    assert "My hobby is knitting." in obs
    assert any("Alice" in o for o in obs)


def test_query_gold_is_set_of_dia_ids(locomo_fixture):
    corpus = load_locomo(str(locomo_fixture))
    multi = [q for q in corpus["queries"]
            if q["q"] == "When did they meet?"][0]
    assert set(multi["gold"]) == {"D1:1", "D2:1"}


def test_query_preserves_category(locomo_fixture):
    corpus = load_locomo(str(locomo_fixture))
    cats = sorted({q["category"] for q in corpus["queries"]})
    assert cats == [1, 3]


def test_source_marks_conversation_and_session(locomo_fixture):
    corpus = load_locomo(str(locomo_fixture))
    by_name = {e["name"]: e for e in corpus["entities"]}
    assert "locomo:0:session_1" in by_name["D1:1"]["_source"]
    assert "locomo:1:session_1" in by_name["D3:1"]["_source"]


def test_evaluate_reports_per_category(locomo_fixture, tmp_path):
    corpus = load_locomo(str(locomo_fixture))
    metrics = evaluate(corpus, memory_dir=str(tmp_path / ".easymem"),
                       top_k=5)
    assert "per_category" in metrics
    assert set(metrics["per_category"].keys()) == {1, 3}
    for cat, m in metrics["per_category"].items():
        assert 0.0 <= m["recall_at_5"] <= 1.0
        assert 0.0 <= m["mrr"] <= 1.0
        assert m["n_queries"] >= 1


def test_evaluate_omits_per_category_when_absent(tmp_path):
    from bench.corpus import build_corpus
    corpus = build_corpus(seed=42, n_entities=20, n_queries=5)
    metrics = evaluate(corpus, memory_dir=str(tmp_path / ".easymem"),
                       top_k=5)
    assert "per_category" not in metrics


import subprocess
import sys


def test_cli_locomo_runs_against_path(locomo_fixture, tmp_path):
    out = subprocess.run(
        [sys.executable, "-m", "bench",
         "--dataset", "locomo",
         "--locomo-path", str(locomo_fixture),
         "--top-k", "5"],
        capture_output=True, text=True,
        cwd=str(_REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr
    import json
    metrics = json.loads(out.stdout)
    assert "recall_at_5" in metrics
    assert "per_category" in metrics
    assert metrics["n_queries"] == 3


def test_cli_locomo_requires_path():
    out = subprocess.run(
        [sys.executable, "-m", "bench", "--dataset", "locomo"],
        capture_output=True, text=True,
        cwd=str(_REPO_ROOT),
    )
    assert out.returncode != 0
    assert "--locomo-path" in (out.stderr + out.stdout)


def test_cli_synthetic_unchanged(tmp_path):
    out = subprocess.run(
        [sys.executable, "-m", "bench",
         "--n-entities", "20", "--n-queries", "5"],
        capture_output=True, text=True,
        cwd=str(_REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr
    import json
    metrics = json.loads(out.stdout)
    assert "per_category" not in metrics
    assert metrics["n_queries"] == 5


@pytest.fixture
def locomo_fixture_with_dates(tmp_path):
    data = [
        {
            "qa": [
                {"question": "What is Alice's hobby?",
                 "answer": "knitting",
                 "evidence": ["D1:2"], "category": 1},
                {"question": "When did they meet?",
                 "answer": "Tuesday",
                 "evidence": ["D1:1", "D2:1"], "category": 3},
            ],
            "conversation": {
                "session_1_date_time": "1:56 pm on 8 May, 2023",
                "session_1": [
                    {"speaker": "Alice", "dia_id": "D1:1",
                     "text": "We first met on Tuesday."},
                    {"speaker": "Alice", "dia_id": "D1:2",
                     "text": "My hobby is knitting."},
                ],
                "session_2_date_time": "9:00 am on 25 May, 2023",
                "session_2": [
                    {"speaker": "Bob", "dia_id": "D2:1",
                     "text": "Yes Tuesday it was."},
                ],
            },
            "session_summary": {
                "session_1_summary": "Alice told Bob about her knitting hobby.",
                "session_2_summary": "Bob confirmed the Tuesday meeting.",
            },
            "event_summary": {
                "events_session_1": {
                    "Alice": ["Alice took up knitting as a hobby.",
                              "Alice met Bob on Tuesday."],
                    "Bob": [],
                    "date": "8 May, 2023",
                },
                "events_session_2": {
                    "Bob": ["Bob recalled the Tuesday meeting."],
                    "date": "25 May, 2023",
                },
            },
        },
    ]
    path = tmp_path / "locomo_with_dates.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_session_granularity_one_entity_per_session(
        locomo_fixture_with_dates):
    corpus = load_locomo(str(locomo_fixture_with_dates),
                         granularity="session")
    names = [e["name"] for e in corpus["entities"]]
    assert len(names) == 2
    assert all("session_" in n for n in names)


def test_session_observations_render_speaker_and_text(
        locomo_fixture_with_dates):
    corpus = load_locomo(str(locomo_fixture_with_dates),
                         granularity="session")
    s1 = next(e for e in corpus["entities"] if "session_1" in e["name"])
    text = " | ".join(s1["observations"])
    assert "Alice: We first met on Tuesday." in text
    assert "Alice: My hobby is knitting." in text


def test_session_gold_remaps_dia_ids(locomo_fixture_with_dates):
    corpus = load_locomo(str(locomo_fixture_with_dates),
                         granularity="session")
    multi = [q for q in corpus["queries"]
             if q["q"] == "When did they meet?"][0]
    names = {e["name"] for e in corpus["entities"]}
    assert set(multi["gold"]) <= names
    assert len(set(multi["gold"])) == 2


def test_temporal_observation_added_session_mode(
        locomo_fixture_with_dates):
    corpus = load_locomo(str(locomo_fixture_with_dates),
                         granularity="session")
    s1 = next(e for e in corpus["entities"] if "session_1" in e["name"])
    joined = " | ".join(s1["observations"]).lower()
    assert "8 may 2023" in joined
    assert "may 2023" in joined


def test_turn_mode_inherits_session_date(locomo_fixture_with_dates):
    corpus = load_locomo(str(locomo_fixture_with_dates),
                         granularity="turn")
    d11 = next(e for e in corpus["entities"] if e["name"] == "D1:1")
    joined = " | ".join(d11["observations"]).lower()
    assert "8 may 2023" in joined


def test_turn_granularity_remains_default(locomo_fixture_with_dates):
    corpus = load_locomo(str(locomo_fixture_with_dates))
    names = {e["name"] for e in corpus["entities"]}
    assert "D1:1" in names and "D2:1" in names


def test_session_includes_summary_observation(locomo_fixture_with_dates):
    corpus = load_locomo(str(locomo_fixture_with_dates),
                         granularity="session")
    s1 = next(e for e in corpus["entities"] if "session_1" in e["name"])
    joined = " | ".join(s1["observations"])
    assert "summary: Alice told Bob about her knitting hobby." in joined


def test_session_includes_event_observations(locomo_fixture_with_dates):
    corpus = load_locomo(str(locomo_fixture_with_dates),
                         granularity="session")
    s1 = next(e for e in corpus["entities"] if "session_1" in e["name"])
    joined = " | ".join(s1["observations"])
    assert "event: Alice took up knitting as a hobby." in joined
    assert "event: Alice met Bob on Tuesday." in joined


def test_turn_mode_excludes_session_annotations(locomo_fixture_with_dates):
    # why: session_summary/event_summary are session-scoped; turn entities
    # stay lean. This guards against accidental scope creep.
    corpus = load_locomo(str(locomo_fixture_with_dates),
                         granularity="turn")
    d11 = next(e for e in corpus["entities"] if e["name"] == "D1:1")
    joined = " | ".join(d11["observations"])
    assert "summary:" not in joined
    assert "event:" not in joined
