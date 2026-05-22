import json
import subprocess
import sys
from pathlib import Path

from semantic_server.text import SYNONYM_MAP, expand_synonyms


def test_default_synonyms_loaded():
    assert len(SYNONYM_MAP) >= 20, (
        "Seed synonyms must populate SYNONYM_MAP at import time"
    )


def test_auth_paraphrase_resolves_to_canonical():
    assert expand_synonyms("signin") == expand_synonyms("login")


def test_db_paraphrase_resolves_to_canonical():
    assert expand_synonyms("postgres") == expand_synonyms("psql")


def test_unknown_word_is_identity():
    assert expand_synonyms("zzzunknownzzz") == "zzzunknownzzz"


def test_aliases_cli_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".easymem").mkdir()
    cli = str(Path(__file__).resolve().parents[1] / "easymem-cli.py")

    subprocess.run([sys.executable, cli, "aliases", "add",
                    "deploy", "ship", "release"], check=True)
    data = json.loads((tmp_path / ".easymem" / "aliases.json").read_text())
    groups = data["groups"]
    assert ["deploy", "ship", "release"] in groups
