import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = str(ROOT / "easymem-cli.py")
MAINTENANCE = str(ROOT / "maintenance.py")


def _seed_project(tmp_path):
    mem = tmp_path / ".easymem"
    mem.mkdir()
    graph = mem / "graph.jsonl"
    # Recent timestamps so prune_entities (MAX_AGE_DAYS=90) keeps them.
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    entries = [
        {"type": "entity", "name": "AuthService",
         "entityType": "service",
         "observations": ["Handles login JWT tokens"],
         "_created": now, "_updated": now},
        {"type": "entity", "name": "SyncManager",
         "entityType": "component",
         "observations": ["Uses LWW conflict resolution"],
         "_created": now, "_updated": now},
    ]
    with open(graph, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    subprocess.run(
        [sys.executable, MAINTENANCE, str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    return mem


def test_search_returns_results(tmp_path):
    _seed_project(tmp_path)
    out = subprocess.run(
        [sys.executable, CLI, "search", "authentication"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "AuthService" in out.stdout


def test_search_handles_paraphrase(tmp_path):
    """Vector layer should find AuthService for 'sign in flow'."""
    _seed_project(tmp_path)
    out = subprocess.run(
        [sys.executable, CLI, "search", "sign in flow"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "AuthService" in out.stdout


def test_search_falls_back_to_tfidf_when_vec_missing(tmp_path):
    _seed_project(tmp_path)
    (tmp_path / ".easymem" / "vec_index.npz").unlink()
    out = subprocess.run(
        [sys.executable, CLI, "search", "JWT"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "AuthService" in out.stdout


def test_date_stem_canonicalizes_across_formats(tmp_path):
    # why: query "May 2023" should hit entity indexed with "8 May, 2023"
    # via the canonical date_YYYY_MM_DD stem emitted by both indexer
    # and tokenizer.
    mem = tmp_path / ".easymem"
    mem.mkdir()
    graph = mem / "graph.jsonl"
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    entries = [
        {"type": "entity", "name": "MeetingNotes",
         "entityType": "log",
         "observations": ["Discussed roadmap on 8 May, 2023 at HQ."],
         "_created": now, "_updated": now},
        {"type": "entity", "name": "Distractor",
         "entityType": "log",
         "observations": ["Unrelated content."],
         "_created": now, "_updated": now},
    ]
    with open(graph, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    subprocess.run(
        [sys.executable, MAINTENANCE, str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    from semantic_server.search import search
    out = search("2023-05-08", str(mem), top_k=3)
    names = [r["entity"] for r in out["results"]]
    assert "MeetingNotes" in names, (
        f"date canonicalization should match across DMY/ISO; got {names}"
    )


def test_per_obs_scoring_undilutes_long_entity(tmp_path):
    # why: under per-obs scoring, a long entity with one tight rare-term obs
    # ranks alongside a short entity matching the same term — old entity-
    # aggregate cosine penalized the long entity by length.
    mem = tmp_path / ".easymem"
    mem.mkdir()
    graph = mem / "graph.jsonl"
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    entries = [
        {"type": "entity", "name": "ShortRare",
         "entityType": "topic",
         "observations": ["The corgi played in the park."],
         "_created": now, "_updated": now},
        {"type": "entity", "name": "LongRare",
         "entityType": "topic",
         "observations": ["The corgi played in the park.",
                          "Unrelated padding sentence one.",
                          "Unrelated padding sentence two.",
                          "Unrelated padding sentence three.",
                          "Unrelated padding sentence four."],
         "_created": now, "_updated": now},
    ]
    with open(graph, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    subprocess.run(
        [sys.executable, MAINTENANCE, str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    from semantic_server.search import search
    out = search("corgi", str(mem), top_k=5)
    names = [r["entity"] for r in out["results"]]
    assert "ShortRare" in names and "LongRare" in names, (
        f"Both should appear; got {names}"
    )


def test_rerank_pool_prefers_rare_term_match(tmp_path):
    # why: re-rank should pull the entity with the rare query term ahead
    # of an entity whose only match is a common stem.
    mem = tmp_path / ".easymem"
    mem.mkdir()
    graph = mem / "graph.jsonl"
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    long_obs = " ".join(["service handles request payload"] * 60)
    entries = [
        {"type": "entity", "name": "Generic",
         "entityType": "service",
         "observations": [long_obs],
         "_created": now, "_updated": now},
        {"type": "entity", "name": "BlipParser",
         "entityType": "service",
         "observations": ["Parses BLIP captions for images."],
         "_created": now, "_updated": now},
    ]
    for _ in range(8):
        entries.append({
            "type": "entity", "name": f"Filler{_}",
            "entityType": "service",
            "observations": ["unrelated payload handling logic"],
            "_created": now, "_updated": now,
        })
    with open(graph, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    subprocess.run(
        [sys.executable, MAINTENANCE, str(tmp_path), "--force"],
        check=True, capture_output=True,
    )
    from semantic_server.search import search
    with_rerank = search("BLIP", str(mem), top_k=1, rerank_pool=10)
    top_with = (with_rerank["results"][0]["entity"]
                if with_rerank["results"] else None)
    assert top_with == "BlipParser", (
        f"rerank should surface BlipParser; got {top_with}"
    )
