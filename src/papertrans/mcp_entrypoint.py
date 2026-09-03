"""Dependency-light entry point for the optional PaperTrans MCP server."""

from __future__ import annotations

import sys


MCP_EXTRA_INSTALL_HINT = "pip install 'papertrans[mcp]'"


def main() -> None:
    """Load the MCP server only after giving base installs an actionable error."""
    try:
        from .mcp_server import main as run_mcp_server
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        print(
            "papertrans-mcp requires the optional MCP dependencies.\n"
            f"Install them with: {MCP_EXTRA_INSTALL_HINT}",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    run_mcp_server()


if __name__ == "__main__":
    main()
