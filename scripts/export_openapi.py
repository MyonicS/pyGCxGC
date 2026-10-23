"""
Write the OpenAPI schema of the app API to ``frontend/openapi.json``.

The TypeScript types of the frontend are generated from this file
(``pnpm gen:api``). ``tests/test_app.py`` fails when the file is out of date.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).parents[1] / "frontend" / "openapi.json"


def schema() -> dict:
    from pyGCxGC.app.server import create_app
    from pyGCxGC.app.session import Session

    session = Session()
    try:
        return create_app(session, "token", 0).openapi()
    finally:
        session.close()


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(dumps(schema()), "utf-8")
    sys.stdout.write(f"Wrote {OUT}\n")
