"""Full LongMemEval-S baseline, per-query to control memory.

Each question has its own ~38-session haystack — we build a fresh
graph per query rather than the 19k-session union that OOMs.
"""
from __future__ import annotations

import contextlib
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

# why: in-process maintenance avoids 500x subprocess + model-load overhead
import maintenance
from semantic_server.search import search

DATASET_PATH = "bench/longmemeval_s.json"


def _seed(memory_dir: str, entities: list[dict]) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, "graph.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for e in entities:
            f.write(json.dumps({"type": "entity", **e}) + "\n")


def _sessions_for_question(q: dict) -> list[dict]:
    ents: list[dict] = []
    sess_ids = q.get("haystack_session_ids") or []
    sess_dates = q.get("haystack_dates") or []
    sess_turns = q.get("haystack_sessions") or []
    for sid, sdate, turns in zip(sess_ids, sess_dates, sess_turns):
        obs = []
        for turn in turns:
            role = turn.get("role", "")
            content = (turn.get("content") or "").strip()
            if content:
                obs.append(f"{role}: {content}")
        ents.append({
            "name": sid,
            "entityType": "session",
            "observations": obs,
            "_source": f"longmemeval:session:{sid}",
            "_session_date": sdate,
        })
    return ents


def main() -> None:
    questions = json.loads(Path(DATASET_PATH).read_text(encoding="utf-8"))
    n = len(questions)
    hits = 0
    rr_sum = 0.0
    latencies_ms: list[float] = []
    records: list[tuple[str, bool, float]] = []
    print(f"[bench] running {n} queries per-query", file=sys.stderr)

    devnull = open(os.devnull, "w")
    try:
        for i, q in enumerate(questions, 1):
            entities = _sessions_for_question(q)
            gold = set(q.get("answer_session_ids") or [])
            cat = q.get("question_type", "")

            with tempfile.TemporaryDirectory() as td:
                mem_dir = os.path.join(td, ".easymem")
                _seed(mem_dir, entities)
                try:
                    with contextlib.redirect_stdout(devnull):
                        maintenance.run(td, force=True)
                except Exception as exc:
                    print(f"[bench] maintenance skipped Q{i}: {exc}",
                          file=sys.stderr)

                t0 = time.perf_counter()
                out = search(q.get("question", ""), mem_dir,
                             top_k=5, rerank_pool=30)
                latencies_ms.append((time.perf_counter() - t0) * 1000)

            results = out.get("results", []) if isinstance(out, dict) else []
            names = [r.get("entity") for r in results
                     if isinstance(r, dict)]
            hit = any(name in gold for name in names)
            if hit:
                hits += 1
            rr = 0.0
            for rank, name in enumerate(names, 1):
                if name in gold:
                    rr = 1.0 / rank
                    break
            rr_sum += rr
            records.append((cat, hit, rr))

            if i % 25 == 0:
                running_r5 = hits / i
                print(f"[bench] {i}/{n}  R@5={running_r5:.3f}",
                      file=sys.stderr)
    finally:
        devnull.close()

    metrics: dict = {
        "recall_at_5": hits / n,
        "mrr": rr_sum / n,
        "latency_p50_ms": statistics.median(latencies_ms),
        "latency_p95_ms": (
            statistics.quantiles(latencies_ms, n=20)[18]
            if len(latencies_ms) >= 20 else max(latencies_ms)
        ),
        "n_queries": n,
    }
    cats = sorted({c for c, _, _ in records if c})
    per_cat: dict = {}
    for cat in cats:
        bucket = [(h, rr) for c, h, rr in records if c == cat]
        per_cat[cat] = {
            "recall_at_5": sum(h for h, _ in bucket) / len(bucket),
            "mrr": sum(rr for _, rr in bucket) / len(bucket),
            "n_queries": len(bucket),
        }
    metrics["per_category"] = per_cat
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
