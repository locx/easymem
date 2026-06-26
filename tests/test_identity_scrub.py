"""Secrets in entity names and relation endpoints must be scrubbed to a
collision-resistant token, never landing raw in graph.jsonl, while distinct
secrets stay distinct and matching raw endpoints still resolve."""
import os

from semantic_server.tools import create_entities, create_relations
from semantic_server.traverse import traverse_relations

_SENTINEL = "ghp_0123456789abcdefghijABCDEFGHIJ0123"
_OTHER = "ghp_zzzzzzzzzzzzzzzzzzzzZZZZZZZZZZ9999"


def _graph_text(memory_dir):
    with open(f"{memory_dir}/graph.jsonl", encoding="utf-8") as f:
        return f.read()


def _md(tmp_path):
    md = str(tmp_path / ".easymem")
    os.makedirs(md, exist_ok=True)
    return md


def test_secret_name_not_in_graph(tmp_path):
    md = _md(tmp_path)
    create_entities([{"name": _SENTINEL, "entityType": "file"}], md)
    create_relations(
        [{"from": "caller", "to": _SENTINEL, "relationType": "uses"}], md)
    assert _SENTINEL not in _graph_text(md)


def test_distinct_secrets_distinct_names(tmp_path):
    md = _md(tmp_path)
    create_entities([
        {"name": _SENTINEL, "entityType": "file"},
        {"name": _OTHER, "entityType": "file"},
    ], md)
    from semantic_server.graph import load_graph_entities
    names = set(load_graph_entities(md))
    secret_names = {n for n in names if n.startswith("[REDACTED:")}
    assert len(secret_names) == 2


def test_relation_resolves_to_scrubbed_entity(tmp_path):
    md = _md(tmp_path)
    create_entities([{"name": _SENTINEL, "entityType": "file"}], md)
    create_relations(
        [{"from": "caller", "to": _SENTINEL, "relationType": "uses"}], md)
    from semantic_server.text import scrub_identity
    scrubbed = scrub_identity(_SENTINEL)
    tr = traverse_relations(scrubbed, md, "both", 1)
    neighbours = {n.get("name") for n in tr.get("nodes", [])}
    assert "caller" in neighbours


def test_scrub_identity_idempotent():
    from semantic_server.text import scrub_identity
    cases = [
        _SENTINEL,
        f"https://user:{_SENTINEL}@host.example",
        "https://user:sk-0123456789abcdefghij0123@host.example",
        "https://user:plainpass@host.example",
        "config.py",
    ]
    for raw in cases:
        once = scrub_identity(raw)
        assert scrub_identity(once) == once
    # distinct raw secrets must still map to distinct tokens
    assert scrub_identity(_SENTINEL) != scrub_identity(_OTHER)


def test_normal_name_unchanged(tmp_path):
    md = _md(tmp_path)
    create_entities([{"name": "config.py", "entityType": "file"}], md)
    from semantic_server.graph import load_graph_entities
    assert "config.py" in load_graph_entities(md)


def _flat(obj):
    """Flatten a response dict's stringified values for substring checks."""
    return repr(obj)


def test_handler_responses_never_echo_raw_identity(tmp_path):
    """Tool responses must echo the scrubbed name, never the raw secret."""
    from semantic_server.tools import (
        create_decision,
        delete_entities,
        rename_entity,
        update_decision_outcome,
    )
    md = _md(tmp_path)

    # secret-shaped decision title
    cd = create_decision(
        {"title": _SENTINEL, "rationale": "because"}, md)
    assert _SENTINEL not in _flat(cd)
    assert "[REDACTED:" in _flat(cd)

    # duplicate-title path also scrubs
    dup = create_decision(
        {"title": _SENTINEL, "rationale": "again"}, md)
    assert _SENTINEL not in _flat(dup)

    upd = update_decision_outcome(
        {"title": _SENTINEL, "outcome": "failed",
         "lesson": "avoid"}, md)
    assert _SENTINEL not in _flat(upd)
    assert "[REDACTED:" in _flat(upd)

    # not-found path on a secret-shaped title
    miss = update_decision_outcome(
        {"title": _OTHER, "outcome": "successful"}, md)
    assert _OTHER not in _flat(miss)

    # secret-shaped entity name through delete + rename
    create_entities([{"name": _OTHER, "entityType": "file"}], md)
    rn = rename_entity(_OTHER, "renamed.py", md)
    assert _OTHER not in _flat(rn)
    assert "[REDACTED:" in _flat(rn)

    create_entities([{"name": _SENTINEL, "entityType": "file"}], md)
    dl = delete_entities([_SENTINEL], md)
    assert _SENTINEL not in _flat(dl)
