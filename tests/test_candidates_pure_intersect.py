from semantic_server.search import _get_candidates


def test_empty_postings_returns_empty_set():
    # why: prior behavior returned all vectors keys on missing postings —
    # a silent full-scan fallback masking malformed indexes.
    assert _get_candidates(["foo"], {}, {"a": {}, "b": {}}) == set()


def test_disjoint_postings_intersect_to_empty():
    # why: prior behavior union-fell-back when intersection was empty,
    # flipping AND→OR and returning irrelevant docs as if matched.
    postings = {"foo": ["d1", "d2"], "bar": ["d3", "d4"]}
    assert _get_candidates(["foo", "bar"], postings, {}) == set()


def test_intersection_returns_overlap_only():
    postings = {"foo": ["d1", "d2", "d3"], "bar": ["d2", "d3", "d4"]}
    assert _get_candidates(["foo", "bar"], postings, {}) == {"d2", "d3"}


def test_single_token_returns_full_posting():
    postings = {"foo": ["d1", "d2"]}
    assert _get_candidates(["foo"], postings, {}) == {"d1", "d2"}


def test_unknown_token_yields_empty():
    postings = {"foo": ["d1"]}
    assert _get_candidates(["bar"], postings, {}) == set()
