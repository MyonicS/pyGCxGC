"""
``pygcxgc mcp [FILES|PROJECT] [-l LIBRARY] [-m MODULATION] [--output DIR]
[--allow-read DIR ...] [--gui]`` (spec MCP-4): the MCP server over stdio, started by the client.

stdout carries only protocol messages (MCP-2): the SDK diverts file
descriptor 1 to stderr while it serves, so the files are opened and the
libraries restored only then (in the server's lifespan), and logs go to the
rotating log file and to stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pygcxgc mcp",
        description="pyGCxGC MCP server (stdio), started by an MCP client",
    )
    p.add_argument("files", nargs="*", help="chromatograms or a .gcxgc project to open")
    p.add_argument("-l", "--library", help="spectral library to load")
    p.add_argument("-m", "--modulation", type=float, help="modulation time (s)")
    p.add_argument(
        "--output",
        default="gcxgc-output",
        help="the only folder the server writes to (default ./gcxgc-output)",
    )
    p.add_argument(
        "--allow-read",
        action="append",
        default=[],
        metavar="DIR",
        help="another folder the server may read (repeatable)",
    )
    p.add_argument("--gui", action="store_true", help="open the GC² window as well")
    p.add_argument("--settings", help="settings file (default: user config dir)")
    p.add_argument(
        "--no-restore", action="store_true", help="do not load the saved libraries"
    )
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        from pyGCxGC.app.paths import settings_file, setup_logging
        from pyGCxGC.app.session import Session, is_project_file, project_files
        from pyGCxGC.mcp.guard import Roots
        from pyGCxGC.mcp.server import build_server
    except ImportError as exc:
        print(  # noqa: T201
            f'The MCP server needs the mcp extra (pip install "pyGCxGC[mcp]"): {exc}',
            file=sys.stderr,
        )
        return 1

    settings = Path(args.settings) if args.settings else settings_file()
    session = Session(settings_path=settings)
    setup_logging(session.log_dir)
    log = logging.getLogger("pyGCxGC.mcp")

    def library_roots():
        paths = list(session.settings.get("libraries", []))
        paths += [r.path for r in session.libraries.values() if r.path]
        return paths

    roots = Roots([Path.cwd(), *args.allow_read], args.output, extra=library_roots)
    for f in args.files:
        roots.allow(f)
        if is_project_file(f):
            with contextlib.suppress(Exception):
                for p in project_files(f):
                    roots.allow(p)
    if args.library:
        roots.allow(args.library)

    params = {"modulation_time": args.modulation} if args.modulation else {}

    @contextlib.asynccontextmanager
    async def lifespan(_server):
        # stdout is the protocol's now; the jobs below run in the background
        if not args.no_restore:
            session.restore_libraries()
        if args.library:
            session.add_library(args.library)
        projects = [f for f in args.files if is_project_file(f)]
        if projects:
            session.open_project(projects[-1], replace_all=False)
        for f in args.files:
            if not is_project_file(f):
                try:
                    session.open_document(f, params=params)
                except Exception as exc:  # reported, the server still starts
                    log.error("Cannot open %s: %s", f, exc)
        if args.gui:
            try:
                app.gui.show()
            except Exception as exc:
                log.error("Cannot open the GC² window: %s", exc)
        try:
            yield {}
        finally:
            app.gui.close()

    app = build_server(session, roots, lifespan)
    log.info("MCP server started (output %s)", roots.output)
    try:
        app.mcp.run("stdio")
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
