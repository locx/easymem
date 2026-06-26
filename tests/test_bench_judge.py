from bench.judge import approx_tokens, run_judge
from bench.run import evaluate


def _corpus():
    return {
        "entities": [
            {"name": "s1", "entityType": "session",
             "observations": ["user: my dog is named Rex",
                              "assistant: nice name"]},
            {"name": "s2", "entityType": "session",
             "observations": ["user: I work as a welder"]},
        ],
        "queries": [
            {"q": "what is the dog's name", "gold": ["s1"],
             "answer": "Rex", "category": "single-session"},
            {"q": "no reference answer here", "gold": ["s2"],
             "category": "single-session"},
        ],
    }


def test_token_savings_offline(tmp_path):
    # why: token-savings needs no LLM — a fake judge that never matches still
    # exercises the offline counting path.
    corpus = _corpus()
    memory_dir = str(tmp_path / ".easymem")
    evaluate(corpus, memory_dir=memory_dir, top_k=5)

    seen: list[str] = []

    def fake_generate(question, context):
        seen.append(context)
        return "Rex"

    def fake_judge(question, gold, predicted):
        return gold.strip().lower() == predicted.strip().lower()

    out = run_judge(corpus, memory_dir, fake_generate, fake_judge, top_k=5)

    # only the query carrying an "answer" is judged
    assert out["n_judged"] == 1
    assert 0.0 <= out["token_savings"] <= 1.0
    assert 0.0 <= out["judge_accuracy"] <= 1.0
    assert seen, "generate should receive retrieved context"


def test_approx_tokens_floor():
    assert approx_tokens("") == 1
    assert approx_tokens("a" * 8) == 2
