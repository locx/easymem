"""Benchmark runner: writes a synthetic graph, runs search per query, measures."""
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from semantic_server.search import search


def _seed_graph(memory_dir: str, entities: list[dict[str, Any]]) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    with open(os.path.join(memory_dir, "graph.jsonl"), "w",
              encoding="utf-8") as f:
        for e in entities:
            f.write(json.dumps({"type": "entity", **e}) + "\n")


def _run_maintenance(memory_dir: str) -> None:
    # why: maintenance.py expects project_dir (parent of .easymem/), not the
    # memory_dir itself — see maintenance.py:540-545.
    root = Path(__file__).resolve().parents[1]
    project_dir = os.path.dirname(os.path.abspath(memory_dir))
    subprocess.run([sys.executable, str(root / "maintenance.py"),
                    project_dir, "--force"], check=True)


def evaluate(corpus: dict, memory_dir: str, top_k: int = 5,
             rerank_pool: int = 30) -> dict:
    _seed_graph(memory_dir, corpus["entities"])
    try:
        _run_maintenance(memory_dir)
    except Exception as exc:
        # why: maintenance failure shouldn't tank the benchmark — TF-IDF still works
        print(f"[bench] maintenance skipped: {exc}", file=sys.stderr)

    hits = 0
    rr_sum = 0.0
    latencies_ms: list[float] = []
    records: list[tuple[Any, bool, float]] = []
    for q in corpus["queries"]:
        t0 = time.perf_counter()
        out = search(q["q"], memory_dir, top_k=top_k,
                     rerank_pool=rerank_pool)
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        # why: search() returns {"results": [...]}; each item has key "entity"
        results = out.get("results", []) if isinstance(out, dict) else []
        names = [r.get("entity") for r in results if isinstance(r, dict)]
        gold = set(q["gold"])
        hit = any(n in gold for n in names)
        if hit:
            hits += 1
        rr = 0.0
        for rank, n in enumerate(names, 1):
            if n in gold:
                rr = 1.0 / rank
                break
        rr_sum += rr
        records.append((q.get("category"), hit, rr))

    n = max(len(corpus["queries"]), 1)
    metrics: dict[str, Any] = {
        "recall_at_5": hits / n,
        "mrr": rr_sum / n,
        "latency_p50_ms": statistics.median(latencies_ms) if latencies_ms else 0,
        "latency_p95_ms": (
            statistics.quantiles(latencies_ms, n=20)[18]
            if len(latencies_ms) >= 20 else max(latencies_ms or [0])
        ),
        "n_queries": n,
    }
    cats = sorted({c for c, _, _ in records if c is not None})
    if cats:
        per_cat: dict[Any, dict[str, float]] = {}
        for cat in cats:
            bucket = [(h, rr) for c, h, rr in records if c == cat]
            n_cat = len(bucket)
            per_cat[cat] = {
                "recall_at_5": sum(h for h, _ in bucket) / n_cat,
                "mrr": sum(rr for _, rr in bucket) / n_cat,
                "n_queries": n_cat,
            }
        metrics["per_category"] = per_cat
    return metrics
