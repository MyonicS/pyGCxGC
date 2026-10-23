"""
Backend for the end-to-end tests: ``python e2e/backend.py --port P --data DIR``.

Writes a synthetic GCxGC-MS run (``DIR/run.cdf``, from ``tests/synthetic.py``)
and serves a fresh session with in-memory settings and the built frontend.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from synthetic import synthetic_run, write_andi  # noqa: E402

from pyGCxGC.app.server import serve  # noqa: E402
from pyGCxGC.app.session import Session  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", default="e2e")
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    data = Path(args.data)
    data.mkdir(parents=True, exist_ok=True)
    raw, _ = synthetic_run()
    write_andi(raw, data / "run.cdf")

    def ready(port: int, _token: str) -> None:
        print(f"READY {port}", flush=True)  # noqa: T201

    serve(Session(), port=args.port, token=args.token, on_ready=ready)


if __name__ == "__main__":
    main()
