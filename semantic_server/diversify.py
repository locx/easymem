"""Post-fusion per-session cap. Preserves input rank."""
from collections import Counter
from typing import Any, Optional


def diversify_by_session(
    rows: list[dict[str, Any]],
    max_per_session: Optional[int],
) -> list[dict[str, Any]]:
    if not max_per_session:
        return rows
    seen: Counter = Counter()
    out: list[dict[str, Any]] = []
    for r in rows:
        sess = r.get("_session")
        if sess is None:
            out.append(r)
            continue
        if seen[sess] >= max_per_session:
            continue
        seen[sess] += 1
        out.append(r)
    return out
