"""
Start the pyGCxGC app server: ``pygcxgc [files] [options]`` or
``python -m pyGCxGC.app [files] [options]``.

Prints ``READY <port>`` on stdout once the server is listening, followed by
the URL with the token. The token can be passed with ``--token`` or the
``PYGCXGC_TOKEN`` environment variable (used by the desktop shell).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def replaces_legacy_tool(tool: str, app_args: list[str]) -> int | None:
    """
    Start the app instead of a deprecated Tk tool (spec NB-5).

    Prints the deprecation notice and runs the app with ``app_args`` (it opens
    in the browser). Returns ``None`` without the ``app`` extra, so the
    caller can fall back to the Tk tool.
    """
    if any(importlib.util.find_spec(m) is None for m in ("fastapi", "uvicorn")):
        print(  # noqa: T201
            f'{tool} is deprecated: install the app (pip install "pyGCxGC[app]") '
            "and run pygcxgc. Starting the old tool.",
            file=sys.stderr,
        )
        return None
    print(  # noqa: T201
        f"{tool} is deprecated and now starts the pyGCxGC app (pygcxgc). "
        f"Run {tool} --legacy for the old tool until it is removed.",
        file=sys.stderr,
    )
    return main([*app_args, "--open"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="pyGCxGC app server (local only)")
    parser.add_argument("files", nargs="*", help="chromatograms to open")
    parser.add_argument("-l", "--library", help="spectral library to load")
    parser.add_argument("-m", "--modulation", type=float, help="modulation time (s)")
    parser.add_argument("--port", type=int, default=0, help="port (default: random)")
    parser.add_argument("--token", default=os.environ.get("PYGCXGC_TOKEN"))
    parser.add_argument("--settings", help="settings file (default: user config dir)")
    parser.add_argument(
        "--no-restore", action="store_true", help="do not load the saved libraries"
    )
    parser.add_argument(
        "--open", action="store_true", help="open the app in the web browser"
    )
    args = parser.parse_args(argv)

    try:
        from platformdirs import user_config_dir

        from pyGCxGC.app.server import serve
        from pyGCxGC.app.session import Session
    except ImportError as exc:
        print(  # noqa: T201
            f"The app needs the 'app' extra (pip install \"pyGCxGC[app]\"): {exc}",
            file=sys.stderr,
        )
        return 1

    settings = args.settings or Path(user_config_dir("pyGCxGC")) / "app.json"
    session = Session(settings_path=settings)
    if not args.no_restore:
        session.restore_libraries()
    if args.library:
        session.add_library(args.library)
    params = {"modulation_time": args.modulation} if args.modulation else {}
    for path in args.files:
        session.open_document(path, params=params)

    def ready(port: int, token: str) -> None:
        url = f"http://127.0.0.1:{port}/?token={token}"
        print(f"READY {port}", flush=True)  # noqa: T201
        print(url, flush=True)  # noqa: T201
        if args.open:
            import webbrowser

            webbrowser.open(url)

    serve(session, port=args.port, token=args.token, on_ready=ready)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
