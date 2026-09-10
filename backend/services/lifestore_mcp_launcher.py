from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    """
    Launch mcp_lifestore/server.py as a real MCP stdio server.

    Why this exists:
    - langchain_mcp_adapters starts local MCP servers as subprocesses.
    - When Python runs mcp_lifestore/server.py directly, backend imports like
      `from core.llm import ...` may not be importable.
    - This launcher adds both project root and backend/ to sys.path first.
    """
    project_root = Path(__file__).resolve().parents[2]
    backend_root = project_root / "backend"
    server_path = project_root / "mcp_lifestore" / "server.py"

    if not server_path.exists():
        raise FileNotFoundError(f"LifeStore MCP server not found at: {server_path}")

    for path in [str(project_root), str(backend_root)]:
        if path not in sys.path:
            sys.path.insert(0, path)

    os.chdir(project_root)

    # Important: run the MCP server as __main__ so its bottom `mcp.run(...)`
    # block executes normally.
    runpy.run_path(str(server_path), run_name="__main__")


if __name__ == "__main__":
    main()
