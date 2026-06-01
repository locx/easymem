import importlib.util
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "smart_recall", ROOT / "hooks" / "smart_recall.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_active_pattern_triples_score():
    sr = _load()
    now = time.time()
    info = {"observations": ["x", "y"], "_updated": "2026-06-01T00:00:00Z"}
    base = sr._score_entity(info, now, {}, "file:auth.py", "main", None)
    assert base > 0
    pat = re.compile(re.escape("auth.py"))
    boosted = sr._score_entity(info, now, {}, "file:auth.py", "main", pat)
    # why: an active-file match must apply the same 3x boost as before.
    assert boosted == base * 3.0
