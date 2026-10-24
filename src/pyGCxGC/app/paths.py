"""
Where the app keeps its files (spec FILE-1, FILE-2).

* Settings: ``app.json`` in the platform's config folder
  (``~/.config/pyGCxGC`` on Linux, ``~/Library/Application Support/pyGCxGC``
  on macOS, ``%LOCALAPPDATA%\\pyGCxGC`` on Windows). The settings of the old
  Tk GCxGC-MS viewer (``ms_viewer.json``) are migrated on first start.
* Caches (converted libraries, structures) and logs: the cache folder of
  :func:`pyGCxGC.ms.library.default_cache_dir`; logs in its ``logs``
  subfolder, as a rotating log file. Nothing is ever sent anywhere.

The folders come from ``platformdirs`` (``app`` extra); without it the XDG
folders are used. This module needs only the core dependencies.
"""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import os
import subprocess
import sys
from pathlib import Path

from pyGCxGC.ms.library import default_cache_dir

APP_NAME = "pyGCxGC"
LOG_FILE = "pygcxgc.log"
#: Settings file of the Tk GCxGC-MS viewer (``pygcxgc-msviewer --legacy``).
LEGACY_MS_VIEWER = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / APP_NAME
    / "ms_viewer.json"
)

log = logging.getLogger("pyGCxGC.app")


def config_dir() -> Path:
    """The platform's config folder for pyGCxGC."""
    with contextlib.suppress(ImportError):
        from platformdirs import user_config_dir

        return Path(user_config_dir(APP_NAME, appauthor=False))
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_NAME


def cache_dir() -> Path:
    """The cache folder (libraries, structures, logs)."""
    return default_cache_dir()


def log_dir(cache: str | Path | None = None) -> Path:
    """The log folder: ``logs`` in the cache folder."""
    return Path(cache or cache_dir()) / "logs"


def settings_file(migrate: bool = True) -> Path:
    """
    The user settings file (``app.json`` in the config folder).

    With ``migrate``, a missing file is created from the settings of the old
    Tk GCxGC-MS viewer (see :func:`migrate_legacy_settings`).
    """
    path = config_dir() / "app.json"
    if migrate:
        migrate_legacy_settings(path)
    return path


def migrate_legacy_settings(
    path: str | Path, legacy: str | Path = LEGACY_MS_VIEWER
) -> bool:
    """
    Create the app settings from ``ms_viewer.json`` (once).

    The viewer's library becomes a saved library and its last data file a
    recent file. Nothing happens when ``path`` exists or ``legacy`` does not.
    Returns whether settings were written.
    """
    path, legacy = Path(path), Path(legacy)
    if path.exists() or not legacy.is_file():
        return False
    try:
        old = json.loads(legacy.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict):
        return False
    settings: dict = {"migrated_from": str(legacy)}
    if isinstance(old.get("library"), str) and old["library"]:
        settings["libraries"] = [old["library"]]
    if isinstance(old.get("data"), str) and old["data"]:
        settings["recent_files"] = [old["data"]]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), "utf-8")
    except OSError:
        return False
    log.info("Migrated the settings of %s", legacy)
    return True


def setup_logging(folder: str | Path | None = None, level: int = logging.INFO) -> Path:
    """
    Log the ``pyGCxGC`` loggers (and uvicorn's errors) to a rotating file.

    Calling it again with the same folder does nothing. Returns the log file.
    """
    folder = Path(folder) if folder else log_dir()
    file = folder / LOG_FILE
    root = logging.getLogger("pyGCxGC")
    for handler in root.handlers:
        if getattr(handler, "_pygcxgc_log", None) == str(file):
            return file
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        return file
    handler._pygcxgc_log = str(file)  # type: ignore[attr-defined]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(min(root.level or level, level))
    logging.getLogger("uvicorn.error").addHandler(handler)
    return file


def open_folder(path: str | Path) -> None:
    """Show a folder in the system's file manager."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen(
            [opener, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


__all__ = [
    "cache_dir",
    "config_dir",
    "log_dir",
    "migrate_legacy_settings",
    "open_folder",
    "settings_file",
    "setup_logging",
]
