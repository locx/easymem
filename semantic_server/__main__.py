"""Entry point for the MCP stdio server: python3 -m semantic_server.

Supported entry point for MCP clients other than Claude Code (Cursor,
Windsurf, Claude Desktop). On Claude Code the CLI + hooks are used instead.
"""
from .server import main

main()
