"""Optional, dev-only end-to-end stage: generate an answer from the retrieved
context and LLM-judge it against the gold answer, plus a fully-offline
token-savings metric.

The generate/judge callables are injected so tests run without a network or
key; the default callables hit the Anthropic API and require an API key that is
read from the environment only. This module must never be imported by the
shipped plugin runtime — it is gated behind `python -m bench --judge`.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from semantic_server.search import search

# Generate a predicted answer from (question, retrieved context).
GenerateFn = Callable[[str, str], str]
# Decide whether a predicted answer matches the gold answer.
JudgeFn = Callable[[str, str, str], bool]


def approx_tokens(text: str) -> int:
    # why: avoid a tokenizer dependency; the ~4-chars/token ratio cancels in
    # the savings ratio, so an approximation is honest for that metric.
    return max(1, len(text) // 4)


def _obs_by_entity(corpus: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in corpus.get("entities", []):
        name = e.get("name")
        if name:
            out[name] = [o for o in (e.get("observations") or [])
                         if isinstance(o, str)]
    return out


def _context_for(names: list[str], obs_by_entity: dict[str, list[str]],
                 max_obs: int = 12) -> str:
    blocks: list[str] = []
    for n in names:
        obs = obs_by_entity.get(n) or []
        if obs:
            blocks.append("\n".join(obs[:max_obs]))
    return "\n\n".join(blocks)


def anthropic_callables(model: str = "claude-haiku-4-5-20251001"
                        ) -> tuple[GenerateFn, JudgeFn]:
    """Build default LLM callables. Lazy-imports the SDK and reads the key from
    the environment so the dependency stays dev-only and out of the runtime."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "the --judge stage needs the 'anthropic' package "
            "(pip install anthropic) — it is a dev-only bench dependency, "
            "not a runtime requirement"
        ) from exc
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "the --judge stage needs ANTHROPIC_API_KEY in the environment"
        )
    client = anthropic.Anthropic()

    def generate(question: str, context: str) -> str:
        msg = client.messages.create(
            model=model, max_tokens=256,
            messages=[{
                "role": "user",
                "content": (
                    f"Context:\n{context}\n\n"
                    f"Answer the question using only the context, as briefly "
                    f"as possible.\nQuestion: {question}"
                ),
            }],
        )
        return "".join(b.text for b in msg.content
                       if getattr(b, "type", "") == "text").strip()

    def judge(question: str, gold: str, predicted: str) -> bool:
        msg = client.messages.create(
            model=model, max_tokens=8,
            messages=[{
                "role": "user",
                "content": (
                    f"Question: {question}\nReference answer: {gold}\n"
                    f"Candidate answer: {predicted}\n\n"
                    f"Does the candidate match the reference? "
                    f"Reply with only YES or NO."
                ),
            }],
        )
        text = "".join(b.text for b in msg.content
                       if getattr(b, "type", "") == "text")
        return text.strip().upper().startswith("YES")

    return generate, judge


def run_judge(corpus: dict, memory_dir: str, generate: GenerateFn,
              judge: JudgeFn, top_k: int = 5,
              limit: int | None = None) -> dict[str, Any]:
    """Run the end-to-end accuracy + token-savings stage. Assumes the graph at
    `memory_dir` is already seeded and indexed (run after `evaluate`)."""
    obs_by_entity = _obs_by_entity(corpus)
    full_tokens = max(1, sum(approx_tokens("\n".join(o))
                             for o in obs_by_entity.values()))

    queries = [q for q in corpus.get("queries", []) if q.get("answer")]
    if limit is not None:
        queries = queries[:limit]

    correct = 0
    ratio_sum = 0.0
    for q in queries:
        out = search(q["q"], memory_dir, top_k=top_k)
        results = out.get("results", []) if isinstance(out, dict) else []
        names = [r.get("entity") for r in results if isinstance(r, dict)]
        context = _context_for(names, obs_by_entity)
        ratio_sum += approx_tokens(context) / full_tokens
        predicted = generate(q["q"], context)
        if judge(q["q"], q["answer"], predicted):
            correct += 1

    n = max(len(queries), 1)
    return {
        "judge_accuracy": correct / n,
        "token_savings": 1.0 - (ratio_sum / n),
        "n_judged": len(queries),
    }
