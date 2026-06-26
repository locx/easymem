"""Run with: python -m bench"""
import argparse
import contextlib
import json
import os
import sys
import tempfile

from .corpus import build_corpus
from .locomo import load_locomo
from .longmemeval import load_longmemeval
from .run import evaluate


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", choices=["synthetic", "locomo", "longmemeval"],
                   default="synthetic")
    p.add_argument("--locomo-path", default=None,
                   help="path to locomo10.json (CC BY-NC; fetch manually)")
    p.add_argument("--longmemeval-path", default=None,
                   help="path to longmemeval_s.json (MIT; fetch manually)")
    p.add_argument("--granularity", choices=["turn", "session"],
                   default="turn",
                   help="locomo entity granularity (session shrinks the "
                        "corpus and remaps gold to session entities)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--n-entities", type=int, default=200)
    p.add_argument("--n-queries", type=int, default=50)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--limit", type=int, default=None,
                   help="cap dataset to first N questions (longmemeval only)")
    p.add_argument("--judge", action="store_true",
                   help="run the dev-only LLM-judge end-to-end stage "
                        "(needs the 'anthropic' package + ANTHROPIC_API_KEY); "
                        "token-savings is reported regardless of accuracy")
    p.add_argument("--judge-model", default="claude-haiku-4-5-20251001",
                   help="model for the --judge stage")
    p.add_argument("--judge-limit", type=int, default=None,
                   help="cap the --judge stage to first N answerable queries")
    args = p.parse_args()

    if args.dataset == "longmemeval":
        if not args.longmemeval_path:
            p.error("--longmemeval-path is required when "
                    "--dataset longmemeval")
        if not os.path.exists(args.longmemeval_path):
            p.error(f"longmemeval-path not found: "
                    f"{args.longmemeval_path}")
        corpus = load_longmemeval(args.longmemeval_path,
                                  limit=args.limit)
    elif args.dataset == "locomo":
        if not args.locomo_path:
            p.error("--locomo-path is required when --dataset locomo")
        if not os.path.exists(args.locomo_path):
            p.error(f"locomo-path not found: {args.locomo_path}")
        # why: --n-entities / --n-queries do not apply to a fixed corpus
        if (args.n_entities != p.get_default("n_entities")
                or args.n_queries != p.get_default("n_queries")):
            print("[bench] note: --n-entities/--n-queries ignored for "
                  "--dataset locomo", file=sys.stderr)
        corpus = load_locomo(args.locomo_path,
                             granularity=args.granularity)
    else:
        corpus = build_corpus(seed=args.seed,
                              n_entities=args.n_entities,
                              n_queries=args.n_queries)

    judge_callables = None
    if args.judge:
        # why: build (and validate key/SDK) before the run so a missing key
        # fails fast on stderr instead of after a full index build.
        from .judge import anthropic_callables, run_judge
        judge_callables = anthropic_callables(model=args.judge_model)

    with tempfile.TemporaryDirectory() as td:
        # why: redirect both Python-level stdout and OS fd 1 so subprocess
        # children (e.g. maintenance.py) also land on stderr — keeps real
        # stdout reserved for the final JSON.
        sys.stdout.flush()
        saved_fd = os.dup(1)
        memory_dir = f"{td}/.easymem"
        try:
            os.dup2(2, 1)
            with contextlib.redirect_stdout(sys.stderr):
                metrics = evaluate(corpus, memory_dir, top_k=args.top_k)
                if judge_callables is not None:
                    generate, judge = judge_callables
                    metrics.update(run_judge(
                        corpus, memory_dir, generate, judge,
                        top_k=args.top_k, limit=args.judge_limit,
                    ))
        finally:
            sys.stdout.flush()
            os.dup2(saved_fd, 1)
            os.close(saved_fd)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
