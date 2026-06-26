"""Contradiction detection in maintenance_utils."""
import json

from semantic_server.maintenance_utils import (
    detect_contradictions,
    write_contradictions_sidecar,
)


def test_flags_negation_cue_mismatch_with_overlap():
    entities = [{
        "name": "SyncManager",
        "observations": [
            "SyncManager uses LWW conflict resolution",
            "SyncManager no longer uses LWW conflict resolution",
        ],
    }]
    findings = detect_contradictions(entities)
    assert "SyncManager" in findings
    assert findings["SyncManager"][0][:2] == [0, 1]


def test_no_flag_when_both_have_cue():
    entities = [{
        "name": "X",
        "observations": [
            "Does not use feature A in module M",
            "Does not use feature A in module M anymore",
        ],
    }]
    assert detect_contradictions(entities) == {}


def test_no_flag_when_no_shared_lexical_content():
    entities = [{
        "name": "X",
        "observations": [
            "Uses PostgreSQL for billing",
            "No queue for notifications",
        ],
    }]
    assert detect_contradictions(entities) == {}


def test_no_flag_for_single_observation():
    entities = [{"name": "X", "observations": ["one obs"]}]
    assert detect_contradictions(entities) == {}


def test_sidecar_roundtrip_and_clear(tmp_path):
    findings = {"E": [[0, 1, 0.5]]}
    write_contradictions_sidecar(str(tmp_path), findings)
    p = tmp_path / "contradictions.json"
    assert p.exists()
    assert json.loads(p.read_text()) == findings

    write_contradictions_sidecar(str(tmp_path), {})
    assert not p.exists()
