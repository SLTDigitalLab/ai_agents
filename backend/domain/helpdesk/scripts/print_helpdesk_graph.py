"""
Print/export the compiled LangGraph flow for the helpdesk agent.

Usage (from backend/):
    python domain/helpdesk/scripts/print_helpdesk_graph.py

Outputs:
  - Mermaid flowchart text printed to stdout (paste into https://mermaid.live
    or any Markdown renderer that supports mermaid code blocks)
  - domain/helpdesk/scripts/helpdesk_graph.mmd   (mermaid source)
  - domain/helpdesk/scripts/helpdesk_graph.png   (rendered image, requires
    internet access to the mermaid.ink API; skipped if it fails)
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# parents[3]: this file -> scripts/ -> helpdesk/ -> domain/ -> backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# fastapi-mail is a runtime/Docker dependency unrelated to graph visualization;
# stub it out so this script can run in a bare local Python env.
if "fastapi_mail" not in sys.modules:
    stub = types.ModuleType("fastapi_mail")
    stub.ConnectionConfig = MagicMock()
    sys.modules["fastapi_mail"] = stub

from domain.archetypes.helpdesk_agent import build_helpdesk_workflow

OUT_DIR = Path(__file__).resolve().parent


def main():
    workflow = build_helpdesk_workflow()
    graph = workflow.compile()
    drawable = graph.get_graph()

    mermaid_text = drawable.draw_mermaid()
    print(mermaid_text)

    mmd_path = OUT_DIR / "helpdesk_graph.mmd"
    mmd_path.write_text(mermaid_text, encoding="utf-8")
    print(f"\nSaved mermaid source to {mmd_path}")

    try:
        png_bytes = drawable.draw_mermaid_png()
        png_path = OUT_DIR / "helpdesk_graph.png"
        png_path.write_bytes(png_bytes)
        print(f"Saved PNG to {png_path}")
    except Exception as exc:
        print(f"Could not render PNG (needs internet access to mermaid.ink): {exc}")


if __name__ == "__main__":
    main()
