"""TF-IDF cosine similarity search and time-based search."""
import heapq
import math
import os as _os
import sys
import time as _time
from collections import Counter

from .config import (
    MAIN_BRANCHES,
    MAX_CACHED_OBS,
    MAX_CANDIDATES,
    MAX_QUERY_CHARS,
    MAX_TOP_K,
    RE_WORDS,
    RRF_K,
    get_current_branch,
    log_event,
    normalize_iso_ts as _normalize_iso_ts,
    session_stats,
)
from .cache import entity_cache as _ec
from .diversify import diversify_by_session
from .graph import load_index, load_graph_entities
from .recall import (
    maybe_reload_recall_counts, record_recalls, recall_counts,
)

from .stem import stem_word as _stem
from .text import (
    expand_synonyms as _expand_synonyms,
    extract_date_stems as _extract_date_stems,
    make_bigrams as _make_bigrams,
    STOPWORDS as _STOPWORDS,
    filter_token as _filter_token,
    load_aliases,
    normalize_type,
)

MIN_SIM_THRESHOLD = 0.001
_HEBBIAN_ALPHA = 0.1

# Alias cache: reload when aliases.json mtime changes
_alias_cache = {"map": None, "mtime": 0.0, "dir": ""}


def _get_alias_map(memory_dir):
    """Return alias-merged synonym map, cached by mtime."""
    aliases_path = _os.path.join(memory_dir, "aliases.json")
    try:
        mt = _os.path.getmtime(aliases_path)
    except OSError:
        mt = 0.0
    if (_alias_cache["map"] is not None
            and _alias_cache["mtime"] == mt
            and _alias_cache["dir"] == memory_dir):
        return _alias_cache["map"]
    merged = load_aliases(memory_dir)
    _alias_cache["map"] = merged
    _alias_cache["mtime"] = mt
    _alias_cache["dir"] = memory_dir
    return merged


def _tokenize_query(query_str, alias_map=None):
    """Tokenize query with stemming, synonyms, bigrams, canonical dates."""
    raw = [w for w in RE_WORDS.findall(query_str.lower()) if _filter_token(w)]
    if alias_map:
        expand = lambda w: alias_map.get(w, w)
    else:
        expand = _expand_synonyms
    # stem then expand — matches indexer order (maintenance.py:148)
    stemmed = [expand(_stem(w)) for w in raw]
    # Include bigrams for compound-term matching
    bigrams = _make_bigrams(stemmed)
    return stemmed + bigrams + _extract_date_stems(query_str)


def _branch_boost(entity_branch, current_branch, sim):
    """Smooth branch relevance factor (no hard cliff).

    Empty entity_branch is treated as 'main' (the default branch).
    """
    eff_entity = entity_branch if entity_branch else "main"
    eff_current = current_branch if current_branch else "main"
    if eff_entity == eff_current:
        return 1.0
    max_penalty = 0.05 if eff_entity in MAIN_BRANCHES else 0.20
    penalty = max_penalty * (1.0 - min(sim, 1.0))
    return 1.0 - penalty


def _enrich_results(results, source, max_obs=MAX_CACHED_OBS):
    """Attach entityType, observations, _branch, _session."""
    for r in results:
        info = source.get(r["entity"], {})
        if info:
            obs = info.get("observations")
            r["entityType"] = normalize_type(info.get("entityType", ""))
            r["_branch"] = info.get("_branch", "")
            r["observations"] = obs[:max_obs] if isinstance(obs, list) else []
            # why: fused stamps _session=None for schema stability; diversified
            # rows fill it from src_map. Fall through to source-derived value
            # only when neither path provided one.
            if r.get("_session") is None:
                r["_session"] = _session_from_source(info.get("_source", ""))


def _session_from_source(source):
    """Recover session id for diversification from a _source string."""
    if not isinstance(source, str) or ":" not in source:
        return None
    parts = source.split(":")
    if len(parts) < 3 or not parts[1]:
        return None
    return parts[1]


def _load_source_map(memory_dir, names):
    """Recover _source for names from the (warm) entity cache."""
    wanted = set(names)
    if not wanted:
        return {}
    # why: the cache now retains _source, so a full graph.jsonl scan per
    # diversified query is no longer needed.
    ents = load_graph_entities(memory_dir)
    out: dict[str, str] = {}
    for name in wanted:
        src = ents.get(name, {}).get("_source")
        if isinstance(src, str) and src:
            out[name] = src
    return out


def _build_query_vector(query, idf, alias_map=None):
    words = _tokenize_query(query, alias_map)
    if not words:
        return None, 0.0
    tf = Counter(words)
    total = len(words)
    query_vec = {}
    for w, count in tf.items():
        weight = (count / total) * idf.get(w, 0)
        if weight > 0:
            query_vec[w] = weight
    mag_q = math.sqrt(sum(v * v for v in query_vec.values()))
    return query_vec, mag_q


def _get_candidates(query_keys, postings, vectors):
    if not postings:
        # why: a full-scan fallback masks a malformed/partial index and amplifies
        # latency to O(N_docs * |query_vec|). Empty candidates surface the bug.
        log_event("INDEX_NO_POSTINGS", "")
        return set()
    # Sort postings smallest-first and intersect incrementally
    # for early short-circuit on broad queries
    relevant = [postings[w] for w in query_keys if w in postings]
    relevant.sort(key=len)
    if not relevant:
        return set()
    if len(relevant) == 1:
        return set(relevant[0])
    # why: pure intersection. The union-fallback flipped AND→OR on empty
    # intersect and returned irrelevant docs as ranked matches.
    candidates = set(relevant[0])
    for pl in relevant[1:]:
        candidates &= set(pl)
        if not candidates:
            break
    return candidates


def _score_candidates(query_vec, mag_q, candidates, vectors, magnitudes,
                      metadata, current_branch, top_k,
                      obs_to_entity=None):
    # why: per-obs index scores each obs separately; aggregate to entity
    # via max-over-obs so a long entity with one well-matching obs ranks
    # alongside a short entity matching the same obs.
    obs_to_entity = obs_to_entity or {}
    by_entity: dict[str, tuple[float, float, float]] = {}
    for name in candidates:
        vec = vectors.get(name)
        if not vec or not isinstance(vec, dict):
            continue
        dot = sum(qw * vec.get(k, 0)
                  for k, qw in query_vec.items() if k in vec)
        if dot == 0.0:
            continue
        mag_b = magnitudes.get(name)
        if mag_b is None:
            mag_b = math.sqrt(sum(v * v for v in vec.values()))
        if mag_b == 0:
            continue
        sim = dot / (mag_q * mag_b)
        if not (sim > MIN_SIM_THRESHOLD and math.isfinite(sim)):
            continue
        ent_name = obs_to_entity.get(name, name)
        entity_branch = metadata.get(ent_name, {}).get("_branch", "")
        if not entity_branch and _ec["data"] and ent_name in _ec["data"]:
            entity_branch = _ec["data"][ent_name].get("_branch", "")
        boost = _branch_boost(entity_branch, current_branch, sim)
        rc = recall_counts.get(ent_name, 0)
        adj_sim = sim * boost * (1.0 + _HEBBIAN_ALPHA * math.log1p(rc))
        prev = by_entity.get(ent_name)
        if prev is None or adj_sim > prev[0]:
            by_entity[ent_name] = (adj_sim, sim, boost)

    heap = []
    for ent, (adj, sim, boost) in by_entity.items():
        if len(heap) < top_k:
            heapq.heappush(heap, (adj, sim, boost, ent))
        elif adj > heap[0][0]:
            heapq.heapreplace(heap, (adj, sim, boost, ent))
    return [
        {"entity": name, "score": round(adj, 4),
         "raw_score": round(raw, 4),
         "branch_boost": round(boost, 4)}
        for adj, raw, boost, name in sorted(heap, reverse=True)
    ]


def _format_results(results, compact, metadata, memory_dir):
    if not results:
        return
    if not compact:
        if _ec["data"] is not None:
            source = _ec["data"]
        elif isinstance(metadata, dict) and metadata:
            source = metadata
        else:
            source = load_graph_entities(memory_dir)
        _enrich_results(results, source, min(5, MAX_CACHED_OBS))
    else:
        for r in results:
            r["entityType"] = normalize_type(
                metadata.get(r["entity"], {}).get("entityType", "")
            )


def _tfidf_search_impl(query, memory_dir, top_k, current_branch, idx):
    """TF-IDF cosine search; idx None means no index built yet."""
    if idx is None:
        return {"error": "No TF-IDF index found. Ensure SessionStart ran.",
                "results": [], "total_indexed": 0}
    vectors = idx.get("vectors", {})
    idf = idx.get("idf", {})
    alias_map = _get_alias_map(memory_dir)
    query_vec, mag_q = _build_query_vector(query, idf, alias_map or None)
    if not query_vec or mag_q == 0:
        return {"results": [], "total_indexed": len(vectors)}
    candidates = _get_candidates(
        query_vec.keys(), idx.get("postings", {}), vectors,
    )
    results = _score_candidates(
        query_vec, mag_q, candidates, vectors,
        idx.get("magnitudes", {}), idx.get("metadata", {}),
        current_branch, top_k,
        obs_to_entity=idx.get("obs_to_entity") or {},
    )
    return {"results": results, "total_indexed": len(vectors),
            "current_branch": current_branch}


_corpus_tok_cache: dict = {"key": None, "data": {}}


def _idf_rerank(query, fused, top_k, idf, current_entities, alias_map):
    """IDF-weighted overlap re-rank; original rank breaks ties."""
    q_toks = set(_tokenize_query(query, alias_map))
    if not q_toks:
        return fused[:top_k]
    # why: corpus tokenization is identical across queries until the graph or
    # aliases change; memoize per entity instead of re-tokenizing each query.
    cache_key = (_ec.get("mtime"), id(alias_map))
    if _corpus_tok_cache["key"] != cache_key:
        _corpus_tok_cache["key"] = cache_key
        _corpus_tok_cache["data"] = {}
    cached = _corpus_tok_cache["data"]
    scored = []
    for orig_rank, r in enumerate(fused):
        name = r["entity"]
        c_toks = cached.get(name)
        if c_toks is None:
            ent = current_entities.get(name) or {}
            obs = ent.get("observations") or []
            text = " ".join(o for o in obs if isinstance(o, str))
            c_toks = set(_tokenize_query(text, alias_map))
            cached[name] = c_toks
        # why: out-of-vocab tokens have no corpus statistics; treat as 0
        # so re-rank only acts on terms the index actually knows.
        weight = sum(idf.get(t, 0.0) for t in (q_toks & c_toks))
        scored.append((-weight, orig_rank, r))
    scored.sort()
    return [r for _, _, r in scored[:top_k]]


def search(query, memory_dir, top_k=5, branch=None, compact=False,
           max_per_session=None, rerank_pool=None):
    """Hybrid search: TF-IDF + vector fused via RRF, phantom-filtered.

    max_per_session caps fused results per originating session for breadth.
    rerank_pool over-fetches and re-ranks by IDF-weighted token overlap.
    """
    _t0 = _time.monotonic()
    query = str(query)[:MAX_QUERY_CHARS] if query else ""
    try:
        top_k = min(max(int(top_k), 1), MAX_TOP_K)
    except (ValueError, TypeError):
        top_k = 5
    try:
        rerank_pool = int(rerank_pool) if rerank_pool else 0
    except (ValueError, TypeError):
        rerank_pool = 0
    current_branch = branch or get_current_branch()
    # Whitespace-only queries can't usefully match anything; skip the
    # full pipeline so neither TF-IDF nor vector pays cost.
    if not query.strip():
        return {"results": [], "total_indexed": 0,
                "current_branch": current_branch}
    maybe_reload_recall_counts()

    # One index read per call — _tfidf_search_impl reuses, phantom-filter reuses.
    idx = load_index(memory_dir)
    # why: re-rank needs enough fused candidates to pick from.
    inner_top_k = (max(rerank_pool * 2, top_k * 4)
                   if rerank_pool > top_k else top_k * 4)
    # why: bound the per-query vector matmul/argsort; MAX_CANDIDATES was
    # imported for exactly this cap but never applied.
    inner_top_k = min(inner_top_k, MAX_CANDIDATES)
    tfidf_out = _tfidf_search_impl(
        query, memory_dir, inner_top_k, current_branch, idx,
    )
    try:
        from .vector import vector_search
        vec_results = vector_search(
            memory_dir, query, top_k=inner_top_k,
        )
    except Exception as exc:
        sys.stderr.write(
            f"[search] vector path failed: {exc} - TF-IDF only\n"
        )
        vec_results = []

    scores: dict[str, float] = {}
    for rank, r in enumerate(tfidf_out.get("results", [])):
        scores[r["entity"]] = (
            scores.get(r["entity"], 0.0) + 1.0 / (RRF_K + rank)
        )
    for rank, (name, _sc) in enumerate(vec_results):
        scores[name] = scores.get(name, 0.0) + 1.0 / (RRF_K + rank)

    current_entities = load_graph_entities(memory_dir)
    metadata = (idx or {}).get("metadata", {})

    # why: top-normalize so the smooth branch-penalty curve uses a per-row
    # signal; constant sim=0.5 made the penalty a flat constant.
    top_rrf = max(scores.values(), default=1.0) or 1.0
    fused = []
    for name, rrf in scores.items():
        if name not in current_entities:
            continue
        eb = (metadata.get(name, {}).get("_branch", "")
              or current_entities.get(name, {}).get("_branch", ""))
        boost = _branch_boost(eb, current_branch, rrf / top_rrf)
        rc = recall_counts.get(name, 0)
        adj = rrf * boost * (1.0 + _HEBBIAN_ALPHA * math.log1p(rc))
        fused.append({
            "entity": name,
            "score": round(adj, 6),
            "raw_score": round(rrf, 6),
            "branch_boost": round(boost, 4),
            # why: stamp _session uniformly so the response schema is stable
            # whether or not diversification ran.
            "_session": None,
        })
    fused.sort(key=lambda r: r["score"], reverse=True)
    if max_per_session:
        # Pull _source only for fused candidates — bounded IO, since the
        # index/metadata path drops _source on load.
        src_map = _load_source_map(
            memory_dir, [r["entity"] for r in fused],
        )
        for r in fused:
            r["_session"] = _session_from_source(
                src_map.get(r["entity"], ""),
            )
        fused = diversify_by_session(fused, max_per_session)
    if rerank_pool > top_k:
        idf = (idx or {}).get("idf") or {}
        alias_map = _get_alias_map(memory_dir)
        results = _idf_rerank(
            query, fused[:rerank_pool], top_k, idf,
            current_entities, alias_map,
        )
    else:
        results = fused[:top_k]
    _format_results(results, compact, metadata, memory_dir)
    if results:
        record_recalls([r["entity"] for r in results])
    session_stats["searches"] += 1
    log_event(
        "SEARCH",
        f'query="{query[:60]}" results={len(results)} '
        f'latency={int((_time.monotonic() - _t0) * 1000)}ms hybrid',
    )
    return {
        "results": results,
        "total_indexed": len(current_entities),
        "current_branch": current_branch,
    }


def search_by_time(memory_dir, since=None, until=None, limit=20, branch_filter=None, entity_type=None):
    """Return entities within a time window, sorted by recency."""
    # why: schema previously had no required field; calling with neither bound
    # returned the whole graph (capped by limit), a non-obvious foot-gun.
    if not since and not until:
        return {"error": "at least one of since/until is required"}
    try: limit = min(max(int(limit), 1), MAX_TOP_K)
    except (ValueError, TypeError): limit = 20
    entities = load_graph_entities(memory_dir)

    since_n = _normalize_iso_ts(since) if since else None
    until_n = _normalize_iso_ts(until) if until else None

    target_type = normalize_type(entity_type) if entity_type else None

    candidates = []
    for name, info in entities.items():
        ts = info.get("_updated") or info.get("_created", "")
        if not ts: continue
        if since_n and ts < since_n: continue
        if until_n and ts > until_n: continue
        if branch_filter and info.get("_branch", "") != branch_filter: continue
        if target_type and normalize_type(info.get("entityType", "")) != target_type: continue
        candidates.append((ts, name))

    total_matched = len(candidates)
    top = heapq.nlargest(limit, candidates)

    results = []
    for ts, name in top:
        info = entities.get(name, {})
        obs = info.get("observations")
        results.append({
            "entity": name,
            "entityType": normalize_type(info.get("entityType", "")),
            "updated": ts,
            "created": info.get("_created", ""),
            "_branch": info.get("_branch", ""),
            "observations": obs[:min(5, MAX_CACHED_OBS)] if isinstance(obs, list) else [],
        })

    session_stats["searches"] += 1
    log_event("TIME_SEARCH", f"since={since} until={until} matched={total_matched}")
    return {"results": results, "total_matched": total_matched}
