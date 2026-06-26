"""LongMemEval-S loader. Output shape matches bench/locomo.py: dict with
`entities` (list[dict]) and `queries` (list[dict])."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _session_observations(session_turns: list[dict]) -> list[str]:
    obs: list[str] = []
    for turn in session_turns:
        role = turn.get("role", "")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        obs.append(f"{role}: {content}")
    return obs


def load_longmemeval(path: str, limit: int | None = None
                     ) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if limit is not None:
        raw = raw[:limit]
    entities: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    seen_session_ids: set[str] = set()

    for q in raw:
        sess_ids = q.get("haystack_session_ids") or []
        sess_dates = q.get("haystack_dates") or []
        sess_turns = q.get("haystack_sessions") or []
        for sid, sdate, turns in zip(sess_ids, sess_dates, sess_turns):
            if sid in seen_session_ids:
                continue
            seen_session_ids.add(sid)
            entities.append({
                "name": sid,
                "entityType": "session",
                "observations": _session_observations(turns),
                "_source": f"longmemeval:session:{sid}",
                "_session_date": sdate,
            })

        queries.append({
            "q": q.get("question", ""),
            "gold": q.get("answer_session_ids") or [],
            "answer": str(q.get("answer", "")),
            "category": q.get("question_type", ""),
            "question_id": q.get("question_id", ""),
        })

    return {"entities": entities, "queries": queries}
