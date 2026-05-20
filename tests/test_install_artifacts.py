"""Validate install.sh produces all expected artifacts.

Run AFTER ./install.sh. Skips cleanly if install hasn't run.
"""
import json
import subprocess
from pathlib import Path

import pytest

VENV = Path.home() / ".claude" / "memory" / "venv"
VENV_PY_FILE = Path.home() / ".claude" / "memory" / ".venv-python"
MANIFEST = Path.home() / ".claude" / "memory" / ".install-manifest"


@pytest.fixture(scope="module")
def installed():
    if not VENV.exists():
        pytest.skip("install.sh has not been run")


def test_venv_exists(installed):
    assert (VENV / "bin" / "python3").exists()


def test_venv_python_sidecar(installed):
    assert VENV_PY_FILE.exists()
    path = VENV_PY_FILE.read_text().strip()
    assert Path(path).exists()


def test_manifest_well_formed(installed):
    data = json.loads(MANIFEST.read_text())
    assert "model" in data
    assert "model_rev" in data
    assert "installed_at" in data


def test_model2vec_importable(installed):
    py = VENV / "bin" / "python3"
    out = subprocess.run(
        [str(py), "-c", "import model2vec; print('ok')"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0
    assert "ok" in out.stdout


def test_model_in_hf_cache(installed):
    py = VENV / "bin" / "python3"
    out = subprocess.run(
        [str(py), "-c",
         "from model2vec import StaticModel; "
         "m = StaticModel.from_pretrained("
         "'minishlab/potion-retrieval-32M'); print(m.dim)"],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0
    # Model's native dim (varies by model); we truncate to EMBED_DIM=256
    # downstream. Here we just verify the model loaded and reports a dim.
    dim = out.stdout.strip()
    assert dim.isdigit() and int(dim) > 0
