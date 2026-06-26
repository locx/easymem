from bench.corpus import build_corpus
from bench.run import evaluate


def test_corpus_shape():
    corpus = build_corpus(seed=42, n_entities=50, n_queries=10)
    assert len(corpus["entities"]) == 50
    assert len(corpus["queries"]) == 10
    for q in corpus["queries"]:
        assert q["gold"]
        assert all(g in {e["name"] for e in corpus["entities"]}
                   for g in q["gold"])


def test_evaluate_returns_metrics(tmp_path):
    corpus = build_corpus(seed=42, n_entities=50, n_queries=10)
    metrics = evaluate(corpus, memory_dir=str(tmp_path / ".easymem"),
                       top_k=5)
    assert 0.0 <= metrics["recall_at_5"] <= 1.0
    assert 0.0 <= metrics["mrr"] <= 1.0
    assert metrics["latency_p50_ms"] >= 0
    assert metrics["latency_p95_ms"] >= 0
