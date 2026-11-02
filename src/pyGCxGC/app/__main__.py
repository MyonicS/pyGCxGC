"""
Start the GC² app: ``gc-squared [files] [options]`` (or ``pygcxgc``), or
``python -m pyGCxGC.app [files] [options]`` (spec NB-5).

The app opens in a desktop window when pywebview is installed, otherwise in
the web browser. ``--headless`` only serves: it prints ``READY <port>`` on
stdout once the server is listening, followed by the URL with the token (used
by the desktop shell). The token can be passed with ``--token`` or the
``PYGCXGC_TOKEN`` environment variable. Files ending in ``.gcxgc`` are
opened as projects.
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

    Prints the deprecation notice and runs the app with ``app_args``. Returns
    ``None`` without the ``app`` extra, so the caller can fall back to the Tk
    tool.
    """
    if any(importlib.util.find_spec(m) is None for m in ("fastapi", "uvicorn")):
        print(  # noqa: T201
            f'{tool} is deprecated: install the app (pip install "pyGCxGC[app]") '
            "and run gc-squared. Starting the old tool.",
            file=sys.stderr,
        )
        return None
    print(  # noqa: T201
        f"{tool} is deprecated and now starts the GC² app (gc-squared). "
        f"Run {tool} --legacy for the old tool until it is removed.",
        file=sys.stderr,
    )
    return main(app_args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gc-squared", description="GC², the pyGCxGC app (local only)"
    )
    parser.add_argument("files", nargs="*", help="chromatograms or a .gcxgc project")
    parser.add_argument("-l", "--library", help="spectral library to load")
    parser.add_argument("-m", "--modulation", type=float, help="modulation time (s)")
    parser.add_argument("--port", type=int, default=0, help="port (default: random)")
    parser.add_argument("--token", default=os.environ.get("PYGCXGC_TOKEN"))
    parser.add_argument("--settings", help="settings file (default: user config dir)")
    parser.add_argument(
        "--no-restore", action="store_true", help="do not load the saved libraries"
    )
    view = parser.add_mutually_exclusive_group()
    view.add_argument(
        "--headless",
        action="store_true",
        help="only serve: print READY <port> and the URL (desktop shell, scripts)",
    )
    view.add_argument(
        "--browser",
        action="store_true",
        help="open the web browser even if pywebview is installed",
    )
    # opening is the default now; kept so that old command lines still work
    view.add_argument("--open", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        import platformdirs  # noqa: F401

        from pyGCxGC.app.paths import settings_file, setup_logging
        from pyGCxGC.app.server import ServerThread, serve
        from pyGCxGC.app.session import Session, is_project_file
    except ImportError as exc:
        print(  # noqa: T201
            f"The app needs the 'app' extra (pip install \"pyGCxGC[app]\"): {exc}",
            file=sys.stderr,
        )
        return 1

    settings = Path(args.settings) if args.settings else settings_file()
    session = Session(settings_path=settings)
    setup_logging(session.log_dir)
    if not args.no_restore:
        session.restore_libraries()
    if args.library:
        session.add_library(args.library)
    params = {"modulation_time": args.modulation} if args.modulation else {}
    projects = [f for f in args.files if is_project_file(f)]
    if projects:
        session.open_project(projects[-1])
    for path in args.files:
        if not is_project_file(path):
            session.open_document(path, params=params)

    if args.headless:

        def ready(port: int, token: str) -> None:
            print(f"READY {port}", flush=True)  # noqa: T201
            print(f"http://127.0.0.1:{port}/?token={token}", flush=True)  # noqa: T201

        serve(session, port=args.port, token=args.token, on_ready=ready)
        return 0

    server = ServerThread(session, args.port, args.token).start()
    print(f"pyGCxGC is running at {server.url}", flush=True)  # noqa: T201
    try:
        if args.browser or not _run_window(server.url):
            import webbrowser

            webbrowser.open(server.url)
            print("Press Ctrl+C to stop.", flush=True)  # noqa: T201
            server.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        session.close()
    return 0


def _run_window(url: str) -> bool:
    """Show the app in a pywebview window until it is closed (False without pywebview)."""
    try:
        import webview
    except ImportError:
        return False
    # exports and figures are downloads
    if hasattr(webview, "settings"):
        webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window("GC²", url, width=1400, height=900)
    webview.start()
    return True


if __name__ == "__main__":
    raise SystemExit(main())
