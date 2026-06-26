"""Entry point for: python3 -m semantic_server (deprecated)."""
import sys

sys.stderr.write(
    "warn: the MCP stdio server is deprecated; use the easymem CLI "
    "(hooks and CLI are the supported entry points)\n"
)

from .server import main  # noqa: E402

main()
