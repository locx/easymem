import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(modname, rel):
    spec = importlib.util.spec_from_file_location(modname, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_secret_and_url_regex_byte_identical():
    # why: the hook and the server keep duplicate scrubber regexes; pin them
    # so a one-sided edit can't silently weaken either copy.
    hook = _load("capture_tool_context", "hooks/capture_tool_context.py")
    from semantic_server import text
    assert hook._SECRET_RE.pattern == text._SECRET_RE.pattern
    assert hook._URL_CRED_RE.pattern == text._URL_CRED_RE.pattern
