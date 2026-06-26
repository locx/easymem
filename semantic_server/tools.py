"""Write operations + intelligence tools.

Includes: create, update, delete entities/relations,
create_decision, update_decision_outcome, graph_stats.
"""
import calendar
import os
import time
from collections import Counter

from .config import (
    MAX_ENTITIES_PER_CALL,
    MAX_OBS_LENGTH,
    MAX_OBS_PER_CALL,
    MAX_RELATIONS_PER_CALL,
    get_current_branch,
    iso_to_epoch,
    log_event,
    now_iso,
    session_stats,
)
from .graph import (
    _obs_dedup_key,
    append_jsonl,
    check_graph_size,
    invalidate_caches,
    invalidate_entity_cache_only,
    invalidate_relation_cache_only,
    load_graph_entities,
    load_graph_relations,
    rewrite_graph_locked,
)
from .text import normalize_name, normalize_type, scrub_secrets

_DECISION_PREFIX = "decision: "

# why: a derived (not persisted) lifecycle view for hygiene surfacing —
# re-recalled entities read as confirmed, old never-recalled ones as stale.
_STATUS_CONFIRM_RECALLS = 2
_STATUS_STALE_DAYS = 30


def _clean_obs_list(obs):
    """Scrub secrets, truncate, and drop blanks — the one observation-write
    gate shared by every in-process write path."""
    return [
        scrub_secrets(o)[:MAX_OBS_LENGTH] for o in obs
        if isinstance(o, str) and o.strip()
    ]


def _build_norm_index(existing_entities):
    """Pre-compute {normalized_name: original_name} for O(1) fuzzy lookup."""
    index = {}
    for name in existing_entities:
        norm = normalize_name(name)
        if norm and len(norm) >= 3:
            index.setdefault(norm, name)
    return index


def _validate_list_arg(val, max_n, label):
    """Validate that val is a list within max_n length; return (list, error_dict)."""
    if not isinstance(val, list):
        return None, {"error": f"{label} must be a list"}
    if len(val) > max_n:
        return None, {"error": f"Max {max_n} {label} per call"}
    return val, None


def _fuzzy_resolve_existing(name, norm_idx):
    """Return similar existing entity name or None."""
    norm = normalize_name(name)
    if not norm or len(norm) < 3:
        return None
    existing = norm_idx.get(norm)
    if existing and existing != name:
        return existing
    return None


def create_entities(entities_input, memory_dir):
    """Create entities via append-only write."""
    entities_input, err = _validate_list_arg(
        entities_input, MAX_ENTITIES_PER_CALL, "entities"
    )
    if err:
        return err
    size_err = check_graph_size(memory_dir)
    if size_err:
        return size_err

    now = now_iso()
    branch = get_current_branch()
    new_entries = []

    for ent in entities_input:
        if not isinstance(ent, dict):
            continue
        name = ent.get("name", "")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue
        etype = normalize_type(ent.get("entityType", ""))
        obs = ent.get("observations", [])
        if not isinstance(obs, list):
            obs = [str(obs)]
        obs = _clean_obs_list(obs)

        new_entries.append({
            "type": "entity",
            "name": name,
            "entityType": etype,
            "observations": obs,
            "_branch": branch,
            "_created": now,
            "_updated": now,
        })

    if not new_entries:
        return {
            "created": 0,
            "message": "No valid entities",
        }

    existing = load_graph_entities(memory_dir)
    norm_index = _build_norm_index(existing)
    similar_warnings = []
    deduped = []
    for entry in new_entries:
        name = entry["name"]
        if name in existing:
            # why: exact-name dup; load-time merge folds observations, so
            # appending another record only bloats the raw JSONL.
            continue
        deduped.append(entry)
        similar = _fuzzy_resolve_existing(name, norm_index)
        if similar:
            similar_warnings.append(
                f"'{name}' similar to existing "
                f"'{similar}'"
            )

    skipped = len(new_entries) - len(deduped)
    new_entries = deduped
    if not new_entries:
        return {"created": 0, "skipped": skipped,
                "message": "All entities already exist"}

    if not append_jsonl(memory_dir, new_entries):
        return {
            "error": "Write failed (lock timeout)",
            "created": 0,
        }
    invalidate_entity_cache_only()

    names = [e["name"] for e in new_entries]
    session_stats["entities_created"] += len(new_entries)
    log_event(
        "CREATE",
        f"{len(new_entries)} entities: {names}",
    )
    result = {"created": len(new_entries)}
    if skipped:
        result["skipped"] = skipped
    if similar_warnings:
        result["similar_entities"] = similar_warnings
        result["hint"] = (
            "Consider using existing entity names "
            "or renaming to avoid duplicates"
        )
    return result


def create_relations(relations_input, memory_dir):
    """Create relations via append-only write."""
    relations_input, err = _validate_list_arg(
        relations_input, MAX_RELATIONS_PER_CALL, "relations"
    )
    if err:
        return err
    size_err = check_graph_size(memory_dir)
    if size_err:
        return size_err

    seen = set()
    new_entries = []
    for rel in relations_input:
        if not isinstance(rel, dict):
            continue
        fr = rel.get("from", "")
        to = rel.get("to", "")
        rt = rel.get("relationType", "")
        if not fr or not to \
                or not isinstance(fr, str) \
                or not isinstance(to, str):
            continue
        if fr == to:
            continue
        key = (fr, to, rt)
        if key in seen:
            continue
        seen.add(key)
        new_entries.append({
            "type": "relation",
            "from": fr,
            "to": to,
            "relationType": rt,
        })

    if not new_entries:
        return {
            "created": 0,
            "message": "No new relations",
        }

    if not append_jsonl(memory_dir, new_entries):
        return {
            "error": "Write failed (lock timeout)",
            "created": 0,
        }
    invalidate_relation_cache_only()

    session_stats["relations_created"] += len(new_entries)
    descs = [
        f"{e['from']}--{e['relationType']}-->"
        f"{e['to']}"
        for e in new_entries[:5]
    ]
    log_event(
        "RELATE",
        f"{len(new_entries)} relations: "
        + ", ".join(descs),
    )
    return {"created": len(new_entries)}


def add_observations(entity_name, observations, memory_dir,
                     _retry=False):
    """Add observations to an existing entity.

    Mtime guard detects concurrent writes — retries once.
    """
    if not isinstance(entity_name, str) \
            or not entity_name:
        return {"error": "entity name required"}
    observations, err = _validate_list_arg(
        observations, MAX_OBS_PER_CALL, "observations"
    )
    if err:
        return err
    size_err = check_graph_size(memory_dir)
    if size_err:
        return size_err

    new_obs = _clean_obs_list(observations)
    if not new_obs:
        return {
            "added": 0,
            "message": "No valid observations",
        }

    now = now_iso()

    graph_path = os.path.join(memory_dir, "graph.jsonl")
    try:
        pre_mtime = os.path.getmtime(graph_path)
    except OSError:
        pre_mtime = 0.0

    cached = load_graph_entities(memory_dir)
    if entity_name not in cached:
        return {
            "error": (
                f"Entity '{entity_name}' not found"
            ),
        }
    info = cached[entity_name]
    cur_obs_keys = {
        _obs_dedup_key(o)
        for o in info.get("observations", [])
    }
    new_obs = [
        o for o in new_obs
        if _obs_dedup_key(o) not in cur_obs_keys
    ]
    if not new_obs:
        return {
            "added": 0,
            "message": "All observations "
                       "already exist",
        }
    etype = info.get("entityType", "")
    created = info.get("_created", now)

    try:
        post_mtime = os.path.getmtime(graph_path)
    except OSError:
        post_mtime = 0.0
    if post_mtime != pre_mtime:
        if not _retry:
            invalidate_caches()
            return add_observations(
                entity_name, observations, memory_dir,
                _retry=True,
            )
        log_event(
            "RACE",
            f'concurrent write on entity="{entity_name}"',
        )
        return {
            "error": "concurrent write",
            "entity": entity_name,
        }

    if not append_jsonl(memory_dir, [{
        "type": "entity",
        "name": entity_name,
        "entityType": etype,
        "observations": new_obs,
        "_created": created,
        "_updated": now,
    }]):
        return {
            "error": "Write failed (lock timeout)",
            "added": 0,
        }
    invalidate_entity_cache_only()

    total = len(new_obs)
    session_stats["observations_added"] += total
    log_event(
        "ADD_OBS",
        f'entity="{entity_name}" added={total}',
    )
    return {"added": total}


def delete_entities(entity_names, memory_dir):
    """Delete entities and cascade-remove relations.

    Loads, transforms, and rewrites under one graph lock so a
    concurrent append can't be lost, and from the full on-disk view
    so other entities keep all their observations.
    """
    entity_names, err = _validate_list_arg(
        entity_names, MAX_ENTITIES_PER_CALL, "entity_names"
    )
    if err:
        return err

    outcome = {}

    def _transform(entities, relations):
        to_delete = {
            n for n in entity_names
            if isinstance(n, str) and n in entities
        }
        if not to_delete:
            return None
        remaining = {
            k: v for k, v in entities.items()
            if k not in to_delete
        }
        kept_rels = [
            r for r in relations
            if r.get("from") not in to_delete
            and r.get("to") not in to_delete
        ]
        outcome["deleted"] = len(to_delete)
        outcome["names"] = list(to_delete)[:5]
        outcome["relations_removed"] = len(relations) - len(kept_rels)
        return remaining, kept_rels

    try:
        rewrite_graph_locked(memory_dir, _transform)
    except OSError:
        return {
            "error": "Write failed (lock timeout)",
            "deleted": 0,
        }

    if not outcome:
        return {
            "deleted": 0,
            "message": "No matching entities found",
        }
    n_del = outcome["deleted"]
    session_stats["entities_deleted"] += n_del
    log_event(
        "DELETE",
        f"{n_del} entities: {outcome['names']}"
        f", {outcome['relations_removed']} relations cascaded",
    )
    return {
        "deleted": n_del,
        "relations_removed": outcome["relations_removed"],
    }


def _build_decision_obs(args):
    """Cleanly extract strings into an observation list for decisions."""
    rationale = args.get("rationale", "")
    obs = [f"Rationale: {rationale[:MAX_OBS_LENGTH]}"]

    alts = args.get("alternatives", [])
    if isinstance(alts, list):
        for alt in alts[:10]:
            if isinstance(alt, str) and alt.strip():
                obs.append(
                    f"Alternative rejected: {alt[:MAX_OBS_LENGTH]}"
                )

    scope = args.get("scope", "")
    if isinstance(scope, str) and scope.strip():
        obs.append(f"Scope: {scope[:MAX_OBS_LENGTH]}")

    chosen = args.get("chosen", "")
    if isinstance(chosen, str) and chosen.strip():
        obs.append(f"Chosen: {chosen[:MAX_OBS_LENGTH]}")

    outcome = args.get("outcome", "pending")
    warnings = []
    if outcome not in (
        "pending", "successful", "failed", "revised",
        "adopted", "rejected", "deferred", "obsolete",
    ):
        outcome = "pending"
        warnings.append("invalid outcome coerced to pending")
    obs.append(f"Outcome: {outcome}")
    # why: scrubbing happens at the create_entities write gate this feeds.
    return obs, outcome, warnings


def create_decision(args, memory_dir):
    """Create a structured decision entity with relations."""
    if not isinstance(args, dict):
        return {"error": "arguments must be a dict"}

    title = args.get("title", "")
    if not title or not isinstance(title, str):
        return {"error": "title is required"}

    rationale = args.get("rationale", "")
    if not rationale or not isinstance(rationale, str):
        return {"error": "rationale is required"}

    obs, outcome, obs_warnings = _build_decision_obs(args)

    entity_name = f"{_DECISION_PREFIX}{title}"
    # why: create_entities does not reject duplicate names — re-appending merges
    # observations at load time and silently overwrites the prior Outcome line.
    if entity_name in load_graph_entities(memory_dir):
        return {
            "error": f"decision '{title}' already exists",
            "existing": entity_name,
            "hint": "use update_decision_outcome",
        }
    result = create_entities(
        [{
            "name": entity_name,
            "entityType": "decision",
            "observations": obs,
        }],
        memory_dir,
    )
    if "error" in result:
        return result

    related = args.get("related_entities", [])
    rel_result = None
    if isinstance(related, list) and related:
        rel_entries = []
        for target in related[:10]:
            if isinstance(target, str) \
                    and target.strip():
                rel_entries.append({
                    "from": entity_name,
                    "to": target,
                    "relationType": "decided-for",
                })
        if rel_entries:
            rel_result = create_relations(
                rel_entries, memory_dir
            )

    log_event(
        "DECISION",
        f'"{title}" outcome={outcome}',
    )
    resp = {
        "created": result.get("created", 0),
        "decision": entity_name,
        "outcome": outcome,
    }
    if obs_warnings:
        resp["warnings"] = obs_warnings
    if rel_result and "error" in rel_result:
        resp["relations_error"] = rel_result["error"]
    elif rel_result:
        resp["relations_created"] = rel_result.get(
            "created", 0
        )
    return resp


def _mint_failure_guideline(title, lesson, memory_dir):
    """Persist a failed decision's lesson as a guideline so it resurfaces in
    future recalls as a preventive rule. Returns the name, or None on dup."""
    clean = (title[len(_DECISION_PREFIX):]
             if title.startswith(_DECISION_PREFIX) else title)
    name = f"guideline: {clean}"[:200]
    res = create_entities([{
        "name": name,
        "entityType": "guideline",
        "observations": [f"[LESSON] {lesson}",
                         f"From failed decision: {clean}"],
    }], memory_dir)
    return name if res.get("created") else None


def update_decision_outcome(args, memory_dir):
    """Update a decision's outcome and record lesson."""
    if not isinstance(args, dict):
        return {"error": "arguments must be a dict"}

    title = args.get("title", "")
    if not title or not isinstance(title, str):
        return {"error": "title is required"}

    outcome = args.get("outcome", "")
    _valid_outcomes = (
        "successful", "failed", "revised",
        "adopted", "rejected", "deferred",
        "obsolete",
    )
    if outcome not in _valid_outcomes:
        return {
            "error": "outcome must be one of: "
                     + ", ".join(_valid_outcomes)
        }

    lesson = args.get("lesson", "")

    if title.startswith(_DECISION_PREFIX):
        candidates = [title]
    else:
        candidates = [
            f"{_DECISION_PREFIX}{title}",
            title,
        ]

    new_obs = [f"Outcome: {outcome}"]
    if isinstance(lesson, str) and lesson.strip():
        new_obs.append(
            f"Lesson: {lesson[:MAX_OBS_LENGTH]}"
        )

    for entity_name in candidates:
        result = add_observations(
            entity_name, new_obs, memory_dir
        )
        err = result.get("error")
        if isinstance(err, str):
            # why: add_observations already loads the graph; a not-found
            # means try the next candidate, any other error is fatal.
            if "not found" in err:
                continue
            return {
                "error": f"Write failed for '{entity_name}': " + err,
            }
        log_event(
            "OUTCOME",
            f'"{title}" -> {outcome}'
            + (f" lesson: {lesson[:80]}"
               if lesson else ""),
        )
        resp = {
            "updated": entity_name,
            "outcome": outcome,
            "observations_added": result.get(
                "added", 0
            ),
        }
        if outcome == "failed" and isinstance(lesson, str) and lesson.strip():
            minted = _mint_failure_guideline(title, lesson, memory_dir)
            if minted:
                resp["guideline_minted"] = minted
        return resp

    return {
        "error": f"Decision '{title}' not found",
    }


def _file_iso(path):
    """Return mtime as ISO string or None."""
    try:
        mt = os.path.getmtime(path)
        return time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(mt)
        )
    except OSError:
        return None


def _file_kb(path):
    """Return file size in KB or 0."""
    try:
        return os.path.getsize(path) // 1024
    except OSError:
        return 0


def _stats_counts(entities, relations):
    type_counts = Counter(
        normalize_type(info.get("entityType", "unknown")) or "unknown"
        for info in entities.values()
    )
    branch_counts = Counter(
        info.get("_branch", "unknown")
        for info in entities.values()
    )
    return type_counts, branch_counts


def _stats_file_info(memory_dir):
    graph_kb = _file_kb(
        os.path.join(memory_dir, "graph.jsonl")
    )
    index_age = _file_iso(
        os.path.join(memory_dir, "tfidf_index.json")
    )
    index_kb = _file_kb(
        os.path.join(memory_dir, "tfidf_index.json")
    )
    last_maint = _file_iso(
        os.path.join(memory_dir, ".last-maintenance")
    )
    return graph_kb, index_age, index_kb, last_maint


def _stats_pending_count(entities):
    return sum(
        1 for info in entities.values()
        if info.get("entityType") == "decision"
        and not any(
            isinstance(o, str)
            and o.startswith("Outcome: ")
            and not o.startswith("Outcome: pending")
            for o in info.get("observations", [])
        )
    )


def _age_days(stamp, now):
    t = iso_to_epoch(stamp)
    return None if t is None else (now - t) / 86400.0


def _stats_status_breakdown(entities):
    """Derive a coarse active/confirmed/stale view from recall + age signals.

    Not persisted — purely a hygiene summary for status/doctor.
    """
    try:
        from .recall import recall_counts as _rc
    except (ImportError, AttributeError):
        _rc = {}
    now = time.time()
    counts = Counter()
    for name, info in entities.items():
        recalls = _rc.get(name, 0) or 0
        if recalls >= _STATUS_CONFIRM_RECALLS:
            counts["confirmed"] += 1
            continue
        age = _age_days(info.get("_updated") or info.get("_created"), now)
        if recalls == 0 and age is not None and age > _STATUS_STALE_DAYS:
            counts["stale"] += 1
        else:
            counts["active"] += 1
    return dict(counts)


def _stats_recall_summary():
    try:
        from .recall import recall_counts as _rc
        return sorted(
            (
                (n, c) for n, c in _rc.items()
                if isinstance(c, (int, float))
            ),
            key=lambda x: x[1],
            reverse=True,
        )[:10]
    except (ImportError, AttributeError):
        return []


def graph_stats(memory_dir):
    """Return graph health and session stats."""
    entities = load_graph_entities(memory_dir)
    relations = load_graph_relations(memory_dir)

    type_counts, branch_counts = _stats_counts(
        entities, relations
    )
    graph_kb, index_age, index_kb, last_maint = (
        _stats_file_info(memory_dir)
    )
    top_recall = _stats_recall_summary()
    n_pending = _stats_pending_count(entities)

    result = {
        "entities": len(entities),
        "relations": len(relations),
        "graph_size_kb": graph_kb,
        "index_size_kb": index_kb,
        "index_built": index_age or "not built",
        "last_maintenance": last_maint or "never",
        "type_breakdown": dict(
            type_counts.most_common(20)
        ),
        "branch_distribution": dict(
            branch_counts.most_common(10)
        ),
        "current_branch": get_current_branch(),
        "top_by_recall": [
            {"name": n, "recalls": c}
            for n, c in top_recall
        ],
        "pending_decisions": n_pending,
        "status_breakdown": _stats_status_breakdown(entities),
        "session": dict(session_stats),
    }

    log_event(
        "STATS",
        f"{len(entities)} entities, "
        f"{len(relations)} relations, "
        f"{n_pending} pending decisions",
    )
    return result


def insights(memory_dir):
    """Summarize what a project's memory knows: types, status, top entities,
    workflows, and recent decisions."""
    entities = load_graph_entities(memory_dir)
    relations = load_graph_relations(memory_dir)
    type_counts, _ = _stats_counts(entities, relations)
    workflows = [
        n for n, info in entities.items()
        if normalize_type(info.get("entityType", "")) == "workflow"
    ]
    decisions = list_decisions(memory_dir, limit=5)
    recent = (decisions.get("decisions", [])
              if isinstance(decisions, dict) else [])
    return {
        "entities": len(entities),
        "relations": len(relations),
        "type_breakdown": dict(type_counts.most_common(10)),
        "status_breakdown": _stats_status_breakdown(entities),
        "top_by_recall": [
            {"name": n, "recalls": c}
            for n, c in _stats_recall_summary()[:5]
        ],
        "workflows": workflows[:10],
        "recent_decisions": recent[:5],
    }


def list_decisions(memory_dir, stale_days=None, limit=50):
    """List all decisions with status.

    Args:
        stale_days: If set, return only pending decisions
            older than this many days (stale hygiene).
            stale_days=0 returns all pending decisions.
        limit: Max number of decisions to return (default 50).
    """
    if stale_days is not None:
        try:
            stale_days = max(0, float(stale_days))
        except (TypeError, ValueError):
            return {"error": "stale_days must be a number"}

    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 50

    entities = load_graph_entities(memory_dir)
    now_ts = time.time()
    decisions = []
    parse_errors = []
    for name, info in entities.items():
        if info.get("entityType") != "decision":
            continue
        obs = info.get("observations", [])
        outcome = "pending"
        # why: outcomes are append-only; the LAST 'Outcome: ' line is the
        # current one. Breaking on the first reports a stale 'pending'.
        for o in obs:
            if isinstance(o, str) \
                    and o.startswith("Outcome: "):
                outcome = o[9:]
        updated = info.get("_updated", "")

        if stale_days is not None:
            if outcome != "pending":
                continue
            if updated:
                try:
                    ut = calendar.timegm(time.strptime(
                        updated[:19], "%Y-%m-%dT%H:%M:%S"
                    ))
                    age = (now_ts - ut) / 86400
                    if age < stale_days:
                        continue
                except (ValueError, OverflowError):
                    parse_errors.append(
                        f"date parse failed for '{name}': {updated!r}"
                    )

        display = name
        if display.startswith(_DECISION_PREFIX):
            display = display[len(_DECISION_PREFIX):]
        truncated = len(obs) > 5
        decisions.append({
            "title": display,
            "outcome": outcome,
            "observations": obs[:5],
            "observations_truncated": truncated,
            "updated": updated,
        })

    # why: default listing is newest-first; stale-hygiene mode is oldest-first
    # so the most-stale decisions surface at the top for triage.
    decisions.sort(
        key=lambda d: d["updated"],
        reverse=(stale_days is None),
    )
    decisions = decisions[:limit]
    resp = {
        "decisions": decisions,
        "total": len(decisions),
    }
    if parse_errors:
        resp["parse_errors"] = parse_errors
    return resp


def remove_observations(entity_name, observations, memory_dir):
    """Remove specific observations from an entity.

    Runs load+rewrite under one graph lock from the full on-disk
    view — see delete_entities.
    """
    if not isinstance(entity_name, str) \
            or not entity_name:
        return {"error": "entity name required"}
    if not isinstance(observations, list):
        return {"error": "observations must be a list"}

    outcome = {}

    def _transform(entities, relations):
        if entity_name not in entities:
            outcome["error"] = f"Entity '{entity_name}' not found"
            return None
        info = entities[entity_name]
        cur_obs = info.get("observations", [])
        to_remove = {_obs_dedup_key(o) for o in observations}
        kept = [o for o in cur_obs
                if _obs_dedup_key(o) not in to_remove]
        removed = len(cur_obs) - len(kept)
        outcome["removed"] = removed
        if removed == 0:
            return None
        updated = dict(entities)
        updated[entity_name] = {
            **info,
            "observations": kept,
            "_updated": now_iso(),
        }
        return updated, relations

    try:
        rewrite_graph_locked(memory_dir, _transform)
    except OSError:
        return {"error": "Write failed (lock timeout)"}
    if "error" in outcome:
        return {"error": outcome["error"]}
    removed = outcome.get("removed", 0)
    if removed == 0:
        return {"removed": 0,
                "message": "No matching observations"}
    log_event("REMOVE_OBS",
              f'entity="{entity_name}" removed={removed}')
    return {"removed": removed}


def _rewrite_relations_for_rename(rels, old_name, new_name):
    """Rewrite relation list substituting old_name -> new_name.

    Drops self-loops and deduplicates edges.
    Returns (fixed_rels, relations_updated, dropped_self_loops, dropped_dups).
    """
    fixed_rels = []
    seen_rels = set()
    dropped_self_loops = 0
    dropped_dups = 0
    relations_updated = 0
    for r in rels:
        orig_fr = r.get("from", "")
        orig_to = r.get("to", "")
        fr = new_name if orig_fr == old_name else orig_fr
        to = new_name if orig_to == old_name else orig_to
        if fr == to:
            dropped_self_loops += 1
            continue
        rt = r.get("relationType", "")
        key = (fr, to, rt)
        if key in seen_rels:
            dropped_dups += 1
            continue
        seen_rels.add(key)
        fixed_rels.append({
            "from": fr, "to": to, "relationType": rt,
        })
        if orig_fr == old_name or orig_to == old_name:
            relations_updated += 1
    return fixed_rels, relations_updated, dropped_self_loops, dropped_dups


def rename_entity(old_name, new_name, memory_dir):
    """Rename an entity, updating all relation references.

    Drops self-loops and dedups duplicate (from, to, type) edges
    that can arise when both old_name and new_name appear in the
    same relation. Runs load+rewrite under one graph lock from the
    full on-disk view — see delete_entities.
    """
    if not old_name or not new_name:
        return {"error": "old_name and new_name required"}
    if old_name == new_name:
        return {"error": "names are identical"}

    outcome = {}

    def _transform(entities, relations):
        if old_name not in entities:
            outcome["error"] = f"Entity '{old_name}' not found"
            return None
        if new_name in entities:
            outcome["error"] = f"Entity '{new_name}' already exists"
            return None
        updated = {}
        for name, info in entities.items():
            if name == old_name:
                updated[new_name] = {
                    **info, "_updated": now_iso(),
                }
            else:
                updated[name] = info
        fixed_rels, relations_updated, dropped_self_loops, dropped_dups = (
            _rewrite_relations_for_rename(relations, old_name, new_name)
        )
        outcome["relations_updated"] = relations_updated
        outcome["self_loops"] = dropped_self_loops
        outcome["dups"] = dropped_dups
        return updated, fixed_rels

    try:
        rewrite_graph_locked(memory_dir, _transform)
    except OSError:
        return {"error": "Write failed (lock timeout)"}
    if "error" in outcome:
        return {"error": outcome["error"]}
    log_event("RENAME",
              f'"{old_name}" -> "{new_name}"')
    resp = {
        "renamed": old_name,
        "to": new_name,
        "relations_updated": outcome.get("relations_updated", 0),
    }
    if outcome.get("self_loops"):
        resp["self_loops_removed"] = outcome["self_loops"]
    if outcome.get("dups"):
        resp["duplicate_relations_merged"] = outcome["dups"]
    return resp


def recall_with_neighbours(query, memory_dir, top_k=3, branch=None):
    """Search plus 1-hop neighbours for the top hits (the 'recall' verb).

    Shared by the CLI and the MCP server so both expose identical behaviour.
    """
    if not query:
        return {"error": "query required"}
    # why: lazy imports — search imports from this module's siblings; importing
    # them at top would risk a cycle.
    from .search import search
    from .traverse import traverse_relations

    sr = search(query, memory_dir, top_k=top_k, branch=branch, compact=True)
    results = sr.get("results", [])
    if not results:
        return sr
    enriched = []
    for r in results[:top_k]:
        entity = r.get("entity", "")
        tr = traverse_relations(entity, memory_dir, "both", 1)
        connected = [
            {
                "name": n.get("name", ""),
                "type": n.get("entityType", ""),
                "relation": n.get("_relation", ""),
            }
            for n in tr.get("nodes", [])
            if n.get("name") != entity
        ][:5]
        enriched.append({
            "entity": entity,
            "score": r.get("score", 0),
            "entityType": r.get("entityType", ""),
            "connected": connected,
        })
    return {
        "results": enriched,
        "total_indexed": sr.get("total_indexed", 0),
    }
