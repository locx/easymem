"""A failed decision with a lesson auto-mints a guideline entity."""
import json
import os

from semantic_server.tools import create_decision, update_decision_outcome


def _entities(memory_dir):
    out = {}
    with open(f"{memory_dir}/graph.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "entity":
                out[obj["name"]] = obj
    return out


def test_failed_outcome_mints_guideline(tmp_path):
    md = str(tmp_path / ".easymem")
    os.makedirs(md, exist_ok=True)
    create_decision({"title": "use global cache",
                     "rationale": "speed"}, md)

    resp = update_decision_outcome({
        "title": "use global cache",
        "outcome": "failed",
        "lesson": "global cache caused cross-project bleed",
    }, md)

    assert resp.get("guideline_minted") == "guideline: use global cache"
    ents = _entities(md)
    g = ents.get("guideline: use global cache")
    assert g and g["entityType"] == "guideline"
    assert any("LESSON" in o for o in g["observations"])


def test_successful_outcome_mints_nothing(tmp_path):
    md = str(tmp_path / ".easymem")
    os.makedirs(md, exist_ok=True)
    create_decision({"title": "use rrf fusion",
                     "rationale": "robust"}, md)
    resp = update_decision_outcome({
        "title": "use rrf fusion", "outcome": "successful",
        "lesson": "worked well",
    }, md)
    assert "guideline_minted" not in resp
    assert "guideline: use rrf fusion" not in _entities(md)
