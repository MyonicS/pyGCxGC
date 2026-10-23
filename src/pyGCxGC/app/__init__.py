"""
The pyGCxGC app: a session layer with an HTTP/WebSocket API.

* :mod:`pyGCxGC.app.session` holds all document state and needs only the core
  dependencies.
* :mod:`pyGCxGC.app.server` exposes a session over HTTP (needs the ``app``
  extra: ``pip install "pyGCxGC[app]"``). Start it with
  ``python -m pyGCxGC.app``.
"""

from __future__ import annotations

from .session import InvalidRequest, Job, NotFound, Session, SessionError

__all__ = ["InvalidRequest", "Job", "NotFound", "Session", "SessionError"]
