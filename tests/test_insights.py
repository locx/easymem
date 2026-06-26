"""insights() summarizes the graph: types, status, workflows, decisions."""
import os

from semantic_server.tools import create_decision, create_entities, insights


def test_insights_shape(tmp_path):
    md = str(tmp_path / ".easymem")
    os.makedirs(md, exist_ok=True)
    create_entities([
        {"name": "auth.py", "entityType": "file", "observations": ["jwt"]},
        {"name": "wf: login", "entityType": "workflow",
         "observations": ["login -> token"]},
    ], md)
    create_decision({"title": "use jwt", "rationale": "stateless"}, md)

    out = insights(md)
    assert out["entities"] >= 3
    assert "file" in out["type_breakdown"]
    assert "wf: login" in out["workflows"]
    assert sum(out["status_breakdown"].values()) == out["entities"]
    assert isinstance(out["recent_decisions"], list)
