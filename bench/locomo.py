"""LoCoMo (snap-research/locomo) dataset adapter.

Reads locomo10.json from a local path and emits the same
{entities, queries} shape as bench.corpus.
"""
import json
import re
from typing import Any, Literal

_MONTH_RE = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December"
)
_DATE_RE = re.compile(
    rf"on (\d{{1,2}}) ({_MONTH_RE}),\s*(\d{{4}})",
    re.IGNORECASE,
)


def _date_observations(raw: str) -> list[str]:
    if not raw:
        return []
    out = [f"date: {raw}"]
    m = _DATE_RE.search(raw)
    if m:
        day, month, year = m.group(1), m.group(2).lower(), m.group(3)
        out.append(f"date: {day} {month} {year}")
        out.append(f"month: {month} {year}")
    return out


def _iter_sessions(conv: dict) -> list[tuple[str, list, str]]:
    # why: locomo pairs session_N with session_N_date_time; iterate both.
    out: list[tuple[str, list, str]] = []
    for key, turns in conv.items():
        if not isinstance(turns, list):
            continue
        if not key.startswith("session_") or key.endswith("_date_time"):
            continue
        date_raw = conv.get(f"{key}_date_time", "") or ""
        out.append((key, turns, date_raw))
    return out


def _session_annotations(top_conv: dict, session_key: str) -> list[str]:
    # why: locomo ships abstractive summary + per-speaker event lists that
    # paraphrase the session in question-friendly language.
    n = session_key.removeprefix("session_")
    out: list[str] = []
    summary = (top_conv.get("session_summary") or {}).get(
        f"session_{n}_summary"
    )
    if isinstance(summary, str) and summary.strip():
        out.append(f"summary: {summary.strip()}")
    events = (top_conv.get("event_summary") or {}).get(
        f"events_session_{n}"
    )
    if isinstance(events, dict):
        for speaker, evs in events.items():
            if speaker == "date" or not isinstance(evs, list):
                continue
            for ev in evs:
                if isinstance(ev, str) and ev.strip():
                    out.append(f"event: {ev.strip()}")
    return out


def load_locomo(
    path: str,
    granularity: Literal["turn", "session"] = "turn",
) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    entities: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []

    for conv_idx, conv in enumerate(data):
        sessions_block = conv.get("conversation") or {}
        dia_to_session: dict[str, str] = {}
        for session_key, turns, date_raw in _iter_sessions(sessions_block):
            date_obs = _date_observations(date_raw)
            source = f"locomo:{conv_idx}:{session_key}"
            if granularity == "session":
                ent_name = f"S:{conv_idx}:{session_key}"
                obs: list[str] = []
                for turn in turns:
                    text = (turn.get("text") or "").strip()
                    if not text:
                        continue
                    dia_id = turn.get("dia_id")
                    if dia_id:
                        dia_to_session[dia_id] = ent_name
                    speaker = (turn.get("speaker") or "").strip()
                    obs.append(f"{speaker}: {text}" if speaker else text)
                obs.extend(date_obs)
                obs.extend(_session_annotations(conv, session_key))
                entities.append({
                    "name": ent_name,
                    "entityType": "session",
                    "observations": obs,
                    "_source": source,
                })
            else:
                for turn in turns:
                    text = (turn.get("text") or "").strip()
                    if not text:
                        continue
                    dia_id = turn.get("dia_id")
                    if not dia_id:
                        continue
                    speaker = (turn.get("speaker") or "").strip()
                    entities.append({
                        "name": dia_id,
                        "entityType": "dialog",
                        "observations": [text, f"speaker: {speaker}",
                                         *date_obs],
                        "_source": source,
                    })

        for qa in conv.get("qa") or []:
            q = qa.get("question")
            if not q:
                continue
            gold_raw = list(qa.get("evidence") or [])
            if granularity == "session":
                seen: set[str] = set()
                gold: list[str] = []
                for d in gold_raw:
                    owner = dia_to_session.get(d)
                    if owner and owner not in seen:
                        seen.add(owner)
                        gold.append(owner)
            else:
                gold = gold_raw
            queries.append({
                "q": q,
                "gold": gold,
                "category": qa.get("category", 0),
                "conv_idx": conv_idx,
            })
    return {"entities": entities, "queries": queries}
