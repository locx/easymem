"""MCP JSON-RPC round-trip: initialize -> tools/list -> tools/call, plus
per-call workspace_root resolution."""
import json
import importlib.resources as res

from bench.run import evaluate
from semantic_server.protocol import TOOLS_SCHEMA_VERSION, handle_message


def _seeded_workspace(tmp_path):
    corpus = {
        "entities": [
            {"name": "auth.py", "entityType": "file",
             "observations": ["JWT validation lives here",
                              "export: login, verify_token"]},
            {"name": "db.py", "entityType": "file",
             "observations": ["postgres connection pool"]},
        ],
        "queries": [{"q": "jwt", "gold": ["auth.py"]}],
    }
    memory_dir = str(tmp_path / "proj" / ".easymem")
    evaluate(corpus, memory_dir=memory_dir, top_k=5)
    return str(tmp_path / "proj"), memory_dir


def test_initialize_and_list(tmp_path):
    md = str(tmp_path / ".easymem")
    init = handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, md)
    assert init["result"]["serverInfo"]["name"]
    assert "tools" in init["result"]["capabilities"]

    listed = handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, md)
    names = {t["name"] for t in listed["result"]["tools"]}
    # CLI-verb aliases must be discoverable alongside the raw tools
    assert {"search", "recall", "decide"} <= names
    assert "semantic_search_memory" in names


def test_tools_call_recall_with_workspace_root(tmp_path):
    workspace, _ = _seeded_workspace(tmp_path)
    # server started in an unrelated dir; the call targets the workspace
    server_dir = str(tmp_path / "elsewhere" / ".easymem")
    resp = handle_message({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "recall",
                   "arguments": {"query": "jwt",
                                 "workspace_root": workspace}},
    }, server_dir)
    assert "result" in resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    names = [r["entity"] for r in payload.get("results", [])]
    assert "auth.py" in names


def test_tools_schema_version_and_shape():
    # Drift guard: the version pins the contract, and the JSON must stay a
    # list of 16 tools so a future shape change trips this test.
    assert TOOLS_SCHEMA_VERSION == "1.0"
    with res.files("semantic_server").joinpath(
            "tools_schema.json").open() as f:
        schema = json.load(f)
    assert isinstance(schema, list)
    assert len(schema) == 16


def test_unknown_tool_errors(tmp_path):
    md = str(tmp_path / ".easymem")
    handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, md)
    resp = handle_message({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    }, md)
    assert resp["error"]["code"] == -32601
