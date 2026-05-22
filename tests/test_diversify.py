from semantic_server.diversify import diversify_by_session


def _row(name, session, rrf):
    return {"name": name, "_session": session, "rrf": rrf}


def test_caps_per_session():
    rows = [
        _row("a1", "s1", 0.9), _row("a2", "s1", 0.8),
        _row("a3", "s1", 0.7), _row("b1", "s2", 0.6),
    ]
    out = diversify_by_session(rows, max_per_session=2)
    names = [r["name"] for r in out]
    assert names == ["a1", "a2", "b1"]


def test_preserves_order_for_unique_sessions():
    rows = [_row("a", "s1", 0.9), _row("b", "s2", 0.8),
            _row("c", "s3", 0.7)]
    out = diversify_by_session(rows, max_per_session=2)
    assert [r["name"] for r in out] == ["a", "b", "c"]


def test_missing_session_treated_as_unique():
    rows = [_row("a", None, 0.9), _row("b", None, 0.8)]
    out = diversify_by_session(rows, max_per_session=1)
    assert [r["name"] for r in out] == ["a", "b"]


def test_disabled_when_max_is_zero_or_none():
    rows = [_row("a", "s1", 0.9), _row("b", "s1", 0.8)]
    assert diversify_by_session(rows, max_per_session=0) == rows
    assert diversify_by_session(rows, max_per_session=None) == rows


def test_search_respects_max_per_session(tmp_path):
    import json
    import maintenance
    from semantic_server.search import search
    from semantic_server import graph as _graph

    mem = tmp_path / ".easymem"
    mem.mkdir()
    lines = []
    for i in range(3):
        lines.append(json.dumps({
            "type": "entity", "name": f"AuthA{i}",
            "entityType": "component",
            "observations": ["handles login"],
            "_source": "episode:sA:1",
        }))
    lines.append(json.dumps({
        "type": "entity", "name": "AuthB",
        "entityType": "component",
        "observations": ["handles login"],
        "_source": "episode:sB:1",
    }))
    (mem / "graph.jsonl").write_text("\n".join(lines) + "\n")

    # Build the TF-IDF index so fusion has candidates.
    maintenance.run(str(tmp_path), force=True)
    _graph.invalidate_caches()

    res = search("login", str(mem), top_k=10, max_per_session=1)
    rows = res["results"] if isinstance(res, dict) else res
    sources = {r.get("_session") for r in rows if isinstance(r, dict)}
    assert "sA" in sources and "sB" in sources
    sa_count = sum(1 for r in rows
                   if isinstance(r, dict) and r.get("_session") == "sA")
    assert sa_count == 1
