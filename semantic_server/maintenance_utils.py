"""Graph maintenance utilities: pruning and consolidation.

Extracted from maintenance.py to reduce bloat.
"""
import json
import math
import re
import sys
import time
import os
from datetime import datetime, timedelta, timezone
from .text import normalize_name, normalize_type, filter_token
from .stem import stem_word

# Configuration defaults (mirrored or passed from maintenance)
_GUARD_AGE_DAYS = 7
_MAX_CONSOLIDATE_ENTITIES = 50_000

try:
    from .config import (MAIN_BRANCHES as _MAIN_BRANCHES,
                         now_iso as _now_iso)
except ImportError:
    _MAIN_BRANCHES = frozenset({"main", "master", "trunk", "develop"})

    def _now_iso():
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def read_recall_counts(memory_dir):
    """Load recall frequency counts from sidecar file."""
    rc_path = os.path.join(memory_dir, "recall_counts.json")
    try:
        with open(rc_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}

def parse_iso_date(s):
    """Parse ISO 8601 to tz-aware datetime (assumes UTC if bare)."""
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def score_entity(entity, now_ts, recall_counts=None, cutoff_str=None, max_age_days=90):
    """Score: obs_count * recency * recall_boost."""
    obs_count = len(entity.get("observations", []))
    if obs_count == 0:
        return 0.0

    updated = entity.get("_updated", "")
    if not updated or (cutoff_str and updated < cutoff_str):
        days = max_age_days
    else:
        dt = parse_iso_date(updated)
        if not dt:
            days = max_age_days
        else:
            days = max(int((now_ts - dt.timestamp()) / 86400), 0)

    recency = 1.0 / (1.0 + days)
    score = obs_count * recency

    if recall_counts:
        rc = recall_counts.get(entity.get("name", ""), 0)
        if rc > 0:
            # why: log1p so the first recall already boosts (log(1)=0
            # made a single recall worthless against decay).
            score *= (1.0 + math.log1p(rc))

    return score

# why: bound the blast radius of one decay pass — a scoring bug or clock
# skew must not be able to wipe the graph unrecoverably.
_PRUNE_CAP_MIN = 100
_PRUNE_CAP_FRACTION = 0.10


def prune_entities(
    entities, relations, recall_counts=None,
    max_age_days=90, decay_threshold=0.1,
    episode_decay_days=14, episode_survival_recall=2,
    min_prune_age_days=30,
):
    """Remove low-score entities with zero inbound relations.

    Non-episode entities younger than min_prune_age_days never prune;
    one pass prunes at most max(_PRUNE_CAP_MIN, 10% of entities),
    lowest scores first (excess candidates defer to later passes).
    """
    has_inbound = {r.get("to", "") for r in relations}
    now_ts = time.time()
    cutoff_dt = (datetime.now(timezone.utc) - timedelta(days=max_age_days))
    cutoff_str = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    floor_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=min_prune_age_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = []
    blank_dropped = 0
    drop_idx = set()
    warned_ts = False
    # why: loop-invariant; bind once instead of per episode iteration.
    EPISODE_DECAY_DAYS = episode_decay_days
    EPISODE_SURVIVAL_RECALL = episode_survival_recall
    for idx, e in enumerate(entities):
        name = e.get("name", "")
        if not name.strip():
            blank_dropped += 1
            drop_idx.add(idx)
            continue
        # Episode decay: unrecalled episodes prune past episode_decay_days
        if e.get("entityType") == "episode":
            ts = e.get("_updated") or e.get("_created", "")
            if ts:
                try:
                    ep_dt = parse_iso_date(ts)
                    age_days = (now_ts - ep_dt.timestamp()) / 86400
                    rc = (recall_counts or {}).get(name, 0)
                    if (age_days > EPISODE_DECAY_DAYS
                            and rc < EPISODE_SURVIVAL_RECALL):
                        candidates.append((0.0, idx, name))
                        continue
                except Exception:
                    if not warned_ts:
                        sys.stderr.write(
                            "warn: unparseable episode timestamp; "
                            "decay skipped for it\n"
                        )
                        warned_ts = True
        if name in has_inbound:
            continue
        # why: hard recency floor — score-based decay must never delete
        # young entities (a 1-obs entity used to score below threshold
        # at day 10 despite a 90-day max age).
        ts = e.get("_updated") or e.get("_created", "")
        if ts and ts > floor_cutoff:
            continue
        score = score_entity(e, now_ts, recall_counts, cutoff_str, max_age_days)
        if score < decay_threshold:
            candidates.append((score, idx, name))

    cap = max(_PRUNE_CAP_MIN, int(len(entities) * _PRUNE_CAP_FRACTION))
    if len(candidates) > cap:
        candidates.sort(key=lambda c: c[0])
        sys.stderr.write(
            f"warn: prune capped at {cap} this pass; "
            f"{len(candidates) - cap} candidates deferred\n"
        )
        candidates = candidates[:cap]

    pruned_names = {name for _, _, name in candidates}
    drop_idx.update(idx for _, idx, _ in candidates)
    kept = [e for i, e in enumerate(entities) if i not in drop_idx]

    kept_rels = [
        r for r in relations
        if r.get("from") not in pruned_names and r.get("to") not in pruned_names
    ]
    return kept, kept_rels, len(pruned_names) + blank_dropped

def _safe_obs_dedup(observations):
    """Deduplicate observations preserving insertion order."""
    if not observations:
        return []
    seen = set()
    result = []
    # why: untagged keys intentionally collapse a string equal to a dict's
    # JSON here (aggressive consolidation), unlike the tagged load-path key.
    for o in observations:
        key = (o if isinstance(o, str) else json.dumps(o, sort_keys=True))
        if key not in seen:
            seen.add(key)
            result.append(o)
    return result

def _can_merge(ent_i, ent_j, norm_i, norm_j, len_i, len_j, guard_cutoff, min_merge):
    if len_i > 2 * len_j or len_j > 2 * len_i:
        return False
    shorter = min(len_i, len_j)
    if shorter < min_merge and norm_i != norm_j:
        return False
    padded_i = f" {norm_i} "
    padded_j = f" {norm_j} "
    if not (norm_i == norm_j or padded_i in padded_j or padded_j in padded_i):
        return False
    bi = ent_i.get("_branch", "")
    bj = ent_j.get("_branch", "")
    if bi and bj and bi != bj and bi not in _MAIN_BRANCHES and bj not in _MAIN_BRANCHES:
        ci = ent_i.get("_created", "")
        cj = ent_j.get("_created", "")
        if ci and ci > guard_cutoff and cj and cj > guard_cutoff:
            return False
    return True


def _find_merge_groups(entities, keyed, guard_cutoff, min_merge_name_len):
    """O(n²) sliding-window comparison; returns (absorbed set, renames dict, merged_count).

    Mutates entities in-place (merges observations into the surviving entity).
    Respects _MAX_COMPARISONS cap; writes warning to stderr and breaks on cap.
    """
    _MAX_COMPARISONS = 500_000
    n = len(keyed)
    WINDOW = 20 if n < 5000 else 10

    absorbed = set()
    renames = {}
    merged_count = 0
    total_comparisons = 0
    cap_hit = False

    for pos in range(n):
        etype_i, norm_i, idx_i = keyed[pos]
        if idx_i in absorbed or not norm_i.strip():
            continue

        ent_i = entities[idx_i]
        obs_dict_i = None
        len_i = len(norm_i)

        for ahead in range(1, WINDOW + 1):
            total_comparisons += 1
            if total_comparisons > _MAX_COMPARISONS:
                cap_hit = True
                break
            j = pos + ahead
            if j >= n:
                break
            etype_j, norm_j, idx_j = keyed[j]
            if etype_j != etype_i:
                break
            if idx_j in absorbed or not norm_j.strip():
                continue

            ent_j = entities[idx_j]
            len_j = len(norm_j)
            if not _can_merge(ent_i, ent_j, norm_i, norm_j, len_i, len_j,
                               guard_cutoff, min_merge_name_len):
                continue

            if obs_dict_i is None:
                obs_dict_i = _safe_obs_dedup(ent_i.get("observations", []))
                _seen_i = {(o if isinstance(o, str) else json.dumps(o, sort_keys=True))
                           for o in obs_dict_i}

            for o in ent_j.get("observations", []):
                key = (o if isinstance(o, str) else json.dumps(o, sort_keys=True))
                if key not in _seen_i:
                    _seen_i.add(key)
                    obs_dict_i.append(o)

            upd_j = ent_j.get("_updated", "")
            upd_i = ent_i.get("_updated", "")
            if upd_j and (not upd_i or upd_j > upd_i):
                ent_i["_updated"] = upd_j
            absorbed.add(idx_j)
            renames[ent_j.get("name", "")] = ent_i.get("name", "")
            merged_count += 1

        if cap_hit:
            sys.stderr.write(
                f"warn: consolidation cap reached after "
                f"{merged_count} merges, remaining entities skipped\n"
            )
            break
        if obs_dict_i is not None:
            ent_i["observations"] = list(obs_dict_i)

    return absorbed, renames, merged_count


def _apply_merges(entities, absorbed):
    """Return surviving entities with capped observations."""
    kept = [e for i, e in enumerate(entities) if i not in absorbed]
    for e in kept:
        obs = e.get("observations", [])
        if len(obs) > 200:
            e["observations"] = obs[-200:]
    return kept


def _rewrite_relations_post_merge(relations, renames):
    """Resolve transitive renames, dedup, and rewrite relation from/to fields."""
    for k in list(renames):
        v = renames[k]
        while v in renames and renames[v] != v:
            v = renames[v]
        renames[k] = v

    updated_rels = []
    seen_rels = set()
    for r in relations:
        fr, to = r.get("from", ""), r.get("to", "")
        new_fr, new_to = renames.get(fr, fr), renames.get(to, to)
        if new_fr == new_to:
            continue
        rel_key = (new_fr, new_to, r.get("relationType", ""))
        if rel_key not in seen_rels:
            seen_rels.add(rel_key)
            if new_fr != fr or new_to != to:
                r = dict(r, **{"from": new_fr, "to": new_to})
            updated_rels.append(r)
    return updated_rels


def consolidate(entities, relations, min_merge_name_len=4):
    """Merge entities with same type + overlapping names."""
    if len(entities) > _MAX_CONSOLIDATE_ENTITIES:
        return entities, relations, 0

    guard_cutoff = (datetime.now(timezone.utc) - timedelta(days=_GUARD_AGE_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    keyed = sorted(
        [(normalize_type(e.get("entityType", "")), normalize_name(e.get("name", "")), i)
         for i, e in enumerate(entities)]
    )

    absorbed, renames, merged_count = _find_merge_groups(
        entities, keyed, guard_cutoff, min_merge_name_len
    )
    kept = _apply_merges(entities, absorbed)
    updated_rels = _rewrite_relations_post_merge(relations, renames)

    return kept, updated_rels, merged_count


# Lexical cues that flag architectural reversal or boolean negation.
# Multi-word phrases must precede their tokens to win regex alternation.
_CONTRADICTION_CUES = (
    "superseded by", "replaced by", "replaced with",
    "switched to", "migrated to", "moved to", "renamed to",
    "no longer", "instead of", "rather than",
    "removed", "deprecated", "supersedes", "obsolete",
    "abandoned", "dropped", "reverted",
    "broken", "fails", "buggy", "rejected",
    "previously", "originally",
    "not", "doesn't", "don't", "won't", "can't",
    "isn't", "wasn't",
)
_CUE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in _CONTRADICTION_CUES)
    + r")\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"\w+", re.UNICODE)

_MIN_SHARED_STEMS = 3
_MIN_JACCARD = 0.3
_MAX_OBS_PER_ENTITY = 20


def _obs_stems(obs: str) -> set:
    return {stem_word(w) for w in _WORD_RE.findall(obs.lower())
            if filter_token(w)}


def detect_contradictions(entities_iter):
    """Flag obs pairs with asymmetric negation cue + lexical overlap.

    Advisory only; never mutates the graph.
    """
    findings = {}
    for ent in entities_iter:
        obs = [o for o in ent.get("observations", [])
               if isinstance(o, str)]
        if len(obs) < 2:
            continue
        if len(obs) > _MAX_OBS_PER_ENTITY:
            obs = obs[-_MAX_OBS_PER_ENTITY:]
        stems = [_obs_stems(o) for o in obs]
        cues = [bool(_CUE_RE.search(o)) for o in obs]
        n = len(obs)
        pairs = []
        for i in range(n):
            if not stems[i]:
                continue
            for j in range(i + 1, n):
                if cues[i] == cues[j] or not stems[j]:
                    continue
                shared = stems[i] & stems[j]
                if len(shared) < _MIN_SHARED_STEMS:
                    continue
                union = stems[i] | stems[j]
                jaccard = len(shared) / len(union)
                if jaccard < _MIN_JACCARD:
                    continue
                pairs.append([i, j, round(jaccard, 2)])
        if pairs:
            findings[ent.get("name", "")] = pairs
    return findings


def _source_class(src: str) -> str:
    """Coarse source classification used by the auto-resolver."""
    if isinstance(src, str) and src.startswith("episode:"):
        return "episode"
    if isinstance(src, str) and src.startswith("user:"):
        return "user"
    return "other"


def resolve_contradictions(entities, findings: dict):
    """Auto-merge contradictions whose entity is episode-sourced.

    For each conflicting pair `(i, j)` reported by the detector,
    the higher-index observation is treated as newer (append-only
    ordering) and kept verbatim; the older one is prefixed with
    `superseded: ` in place. Entities sourced from `user:*` (or
    anything other than `episode:*`) are left for human review and
    remain in `findings`. Resolved entries are pruned from
    `findings` so the sidecar only carries the unresolved remainder.

    Returns (resolved_count, unresolved_count).
    """
    if isinstance(entities, dict):
        index = entities
    else:
        index = {}
        for ent in entities:
            name = ent.get("name") if isinstance(ent, dict) else None
            if isinstance(name, str) and name:
                index[name] = ent

    resolved = 0
    unresolved = 0
    keep = {}
    for name, pairs in findings.items():
        ent = index.get(name)
        if ent is None or _source_class(ent.get("_source", "")) != "episode":
            unresolved += len(pairs)
            keep[name] = pairs
            continue
        obs = ent.get("observations", [])
        offset = max(0, len(obs) - _MAX_OBS_PER_ENTITY)
        superseded_idx = set()
        for pair in pairs:
            if len(pair) < 2:
                continue
            i, j = pair[0], pair[1]
            # why: i,j index the detector's last-N window; bound them
            # against that window, then translate to the obs index.
            if not (0 <= i < _MAX_OBS_PER_ENTITY
                    and 0 <= j < _MAX_OBS_PER_ENTITY):
                continue
            older = offset + min(i, j)
            if 0 <= older < len(obs):
                superseded_idx.add(older)
        if not superseded_idx:
            unresolved += len(pairs)
            keep[name] = pairs
            continue
        new_obs = []
        for k, o in enumerate(obs):
            if k in superseded_idx and isinstance(o, str) \
                    and not o.startswith("superseded: "):
                new_obs.append("superseded: " + o)
            else:
                new_obs.append(o)
        ent["observations"] = new_obs
        resolved += len(superseded_idx)

    findings.clear()
    findings.update(keep)
    return resolved, unresolved


def write_contradictions_sidecar(memory_dir: str, findings: dict) -> None:
    """Atomic write or clear of .easymem/contradictions.json."""
    path = os.path.join(memory_dir, "contradictions.json")
    if not findings:
        try:
            os.unlink(path)
        except OSError:
            pass
        return
    tmp = path + ".new"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(findings, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def stamp_metadata(entities, branch):
    """Add _branch/_created to new entities."""
    now = _now_iso()
    for e in entities:
        if "_branch" not in e:
            e["_branch"] = branch
        if "_created" not in e:
            e["_created"] = now
        if "_updated" not in e:
            # why: entities sort/score by _updated; seed it at creation.
            e["_updated"] = now
    return entities
