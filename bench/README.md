# easymem retrieval benchmark

Synthetic CC-trace corpus. Not a substitute for LongMemEval-S, but
a stable, hermetic number we can re-run on every change.

## Run

    python -m bench --n-entities 200 --n-queries 50

Outputs JSON with `recall_at_5`, `mrr`, `latency_p50_ms`, `latency_p95_ms`.

## Interpreting

- Recall@5 above 0.85 on the default corpus is the bar.
- p50 latency under 50 ms on a 200-entity graph is the bar.
- Drops on either across commits are regressions.

## Baseline (2026-05-21)

    python -m bench --seed 42 --n-entities 100 --n-queries 30

    recall_at_5:     1.00
    mrr:             1.00
    latency_p50_ms:  0.42
    latency_p95_ms:  326.46  (cold-start; warm runs <2ms)

## LoCoMo dataset

[LoCoMo](https://github.com/snap-research/locomo) is CC BY-NC 4.0 — we do not vendor `locomo10.json`. Fetch once:

    curl -L -o locomo10.json https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

Then run:

    python -m bench --dataset locomo --locomo-path ./locomo10.json --top-k 5

Output includes a `per_category` breakdown alongside the overall metrics.

## LoCoMo baseline (2026-05-22)

    python -m bench --dataset locomo --locomo-path ./locomo10.json --top-k 5

    recall_at_5:     0.241
    mrr:             0.148
    latency_p50_ms:  2.16
    latency_p95_ms:  3.33
    n_queries:       1986

    per_category R@5: cat1=0.223 (n=282)  cat2=0.321 (n=321)
                      cat3=0.167 (n=96)   cat4=0.243 (n=841)  cat5=0.209 (n=446)

## LoCoMo session-granularity baseline (2026-05-22)

    python -m bench --dataset locomo --locomo-path ./locomo10.json --granularity session --top-k 5

    recall_at_5:     0.568   (+136% vs turn mode)
    mrr:             0.413   (+179%)
    latency_p50_ms:  0.97
    latency_p95_ms:  1.46
    n_queries:       1986

    per_category R@5: cat1=0.663 (n=282)  cat2=0.564 (n=321)
                      cat3=0.385 (n=96)   cat4=0.548 (n=841)  cat5=0.587 (n=446)

## LoCoMo + summaries + token-overlap re-ranker (2026-05-22)

    python -m bench --dataset locomo --locomo-path ./locomo10.json --granularity session --top-k 5

    recall_at_5:     0.839   (+248% vs turn baseline; +47.7% vs session-only)
    mrr:             0.711   (+380% vs turn baseline)
    latency_p50_ms:  3.66
    latency_p95_ms:  4.35
    n_queries:       1986

    per_category R@5: cat1=0.809 (n=282)  cat2=0.854 (n=321)
                      cat3=0.510 (n=96)   cat4=0.868 (n=841)  cat5=0.863 (n=446)

## LoCoMo + IDF-weighted re-rank over top-30 (2026-05-22)

    python -m bench --dataset locomo --locomo-path ./locomo10.json --granularity session --top-k 5

    recall_at_5:     0.881   (+265% vs turn baseline; +55% vs session-only)
    mrr:             0.755
    latency_p50_ms:  4.93
    latency_p95_ms:  5.68
    n_queries:       1986

    per_category R@5: cat1=0.816 (n=282)  cat2=0.882 (n=321)
                      cat3=0.531 (n=96)   cat4=0.918 (n=841)  cat5=0.926 (n=446)

## Engine-side: per-obs scoring + date stems + rerank_pool API (2026-05-22)

    python -m bench --dataset locomo --locomo-path ./bench/locomo10.json --granularity session --top-k 5

    recall_at_5:     0.893   (+1.2pp from engine changes; bench rerank still active)
    mrr:             0.764
    latency_p50_ms:  6.03
    latency_p95_ms:  9.05
    n_queries:       1986

    per_category R@5: cat1=0.837 (n=282)  cat2=0.879 (n=321)
                      cat3=0.563 (n=96)   cat4=0.932 (n=841)  cat5=0.935 (n=446)

## Engine-only path after bench/engine dedup (2026-05-22)

    python -m bench --dataset locomo --locomo-path ./bench/locomo10.json --granularity session --top-k 5

    recall_at_5:     0.845   (engine rerank_pool=30 only; bench-side rerank removed)
    mrr:             0.701
    latency_p50_ms:  16.08
    latency_p95_ms:  19.06
    n_queries:       1986

    per_category R@5: cat1=0.791 (n=282)  cat2=0.847 (n=321)
                      cat3=0.531 (n=96)   cat4=0.889 (n=841)  cat5=0.861 (n=446)

## LongMemEval dataset

[LongMemEval](https://github.com/xiaowu0162/LongMemEval) is MIT-licensed. Fetch the cleaned S split once:

    curl -L -o longmemeval_s.json https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json

Then run:

    python -m bench --dataset longmemeval --longmemeval-path ./longmemeval_s.json --top-k 5

Output reports `recall_at_5`, `mrr`, latency percentiles, and per-question-type breakdown.

## LongMemEval-S baseline (2026-05-22, per-query)

Run via `bench/longmemeval_full.py` — each query is evaluated against its own ~38-session haystack (the standard LongMemEval protocol), rather than the 19k-session union of all haystacks. The union mode OOMs the vector-index bootstrap; per-query mode keeps RAM bounded and matches the dataset paper's evaluation semantics.

    python -m bench.longmemeval_full

    recall_at_5:     0.948
    mrr:             0.888
    latency_p50_ms:  116.18
    latency_p95_ms:  157.20
    n_queries:       500

    per_category R@5: knowledge-update=1.000 (n=78)
                      multi-session=0.955 (n=133)
                      single-session-assistant=1.000 (n=56)
                      single-session-preference=0.600 (n=30)
                      single-session-user=1.000 (n=70)
                      temporal-reasoning=0.940 (n=133)

The single-session-preference category is the weakness — 0.60 vs 0.94–1.00 on every other category. Preference queries are short ("what type of rice is my favorite?") and the answer session is one of many similar food/lifestyle sessions; the retrieval signal is weak.

## End-to-end LLM-judge + token-savings (`--judge`)

The retrieval numbers above measure *retrieval* (R@5 / MRR). To report an
end-to-end **answer accuracy** comparable to LLM-judge benchmarks, add
`--judge`: for each query carrying a reference answer, it generates an answer
from the retrieved top-k context and asks an LLM whether it matches the gold
answer. It also reports **token-savings** — retrieved-context tokens vs. the
full store — which is computed offline and needs no LLM.

    python -m bench --dataset longmemeval --longmemeval-path ./longmemeval_s.json --judge --judge-limit 100

Adds `judge_accuracy`, `token_savings`, and `n_judged` to the JSON.

**Dev-only dependency — never shipped.** `--judge` lazy-imports `anthropic`
and reads `ANTHROPIC_API_KEY` from the environment only. It is intentionally
absent from `requirements.txt`: the plugin runtime stays zero-key and
zero-cloud; only the benchmark (which never runs on a user's machine) may call
an LLM. Without the package or key, `--judge` errors immediately and the plain
retrieval run is unaffected. `token_savings` is approximate (≈4 chars/token,
which cancels in the ratio).
