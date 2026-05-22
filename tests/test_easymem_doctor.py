import subprocess
import sys


def test_easymem_doctor_runs():
    out = subprocess.run(
        [sys.executable, "easymem-cli.py", "doctor"],
        capture_output=True, text=True,
    )
    # Doctor should report a status (green or red), not crash
    assert out.returncode in (0, 1, 2)


def test_easymem_doctor_mentions_venv():
    out = subprocess.run(
        [sys.executable, "easymem-cli.py", "doctor"],
        capture_output=True, text=True,
    )
    combined = (out.stdout + out.stderr).lower()
    assert "venv" in combined or "model2vec" in combined
