from semantic_server import tools


def _args(**over):
    base = {
        "title": "use postgres",
        "rationale": "stronger ACID guarantees vs sqlite for production load",
        "outcome": "pending",
    }
    base.update(over)
    return base


def test_first_call_creates_decision(tmp_path):
    result = tools.create_decision(_args(), str(tmp_path))
    assert result.get("created") == 1
    assert result.get("decision") == "decision: use postgres"


def test_duplicate_title_returns_error(tmp_path):
    # why: prior behavior re-appended the entity, merging observations at
    # load time and silently overwriting the existing Outcome line.
    tools.create_decision(_args(), str(tmp_path))
    result = tools.create_decision(_args(outcome="successful"), str(tmp_path))
    assert "error" in result
    assert "already exists" in result["error"]
    assert result.get("existing") == "decision: use postgres"
    assert result.get("hint") == "use update_decision_outcome"
