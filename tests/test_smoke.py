"""Smoke test: ensures pytest harness runs."""


def test_imports():
    import semantic_server.graph as g
    assert hasattr(g, "load_graph_entities")


def test_pytest_ok():
    assert True
