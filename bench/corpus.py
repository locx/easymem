"""Synthetic CC-trace corpus for retrieval benchmarking.

Entities mimic the shape easymem captures from real Claude sessions:
components with stems-matchable observations and paraphrasable query targets.
"""
import random
from typing import Any

_THEMES = [
    ("AuthService", ["handles user login and sessions",
                     "validates JWT tokens"],
     ["how does login work", "where are tokens validated",
      "user authentication flow"]),
    ("CacheLayer", ["memoizes database query results",
                    "TTL of 60 seconds"],
     ["query caching strategy", "how long do cache entries live",
      "memoization layer"]),
    ("SyncManager", ["resolves conflicts via last-write-wins",
                     "handles offline edits"],
     ["conflict resolution policy", "offline edit handling",
      "LWW sync"]),
    ("ApiRouter", ["dispatches HTTP requests to handlers",
                   "applies rate limits"],
     ["request dispatch", "http routing logic",
      "endpoint rate limiting"]),
    ("ConfigLoader", ["reads YAML config from disk",
                      "environment overrides"],
     ["configuration loading", "yaml settings parsing",
      "env override behavior"]),
]


def build_corpus(seed: int = 42, n_entities: int = 50,
                 n_queries: int = 10) -> dict[str, Any]:
    rng = random.Random(seed)
    entities: list[dict[str, Any]] = []
    for i in range(n_entities):
        base = _THEMES[i % len(_THEMES)]
        name = f"{base[0]}_{i}" if i >= len(_THEMES) else base[0]
        entities.append({
            "name": name,
            "entityType": "component",
            "observations": list(base[1]),
        })
    queries: list[dict[str, Any]] = []
    for j in range(n_queries):
        theme = _THEMES[j % len(_THEMES)]
        qtext = rng.choice(theme[2])
        gold = [e["name"] for e in entities
                if e["name"].startswith(theme[0])]
        queries.append({"q": qtext, "gold": gold})
    return {"entities": entities, "queries": queries}
