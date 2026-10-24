"""
The pyGCxGC app: a session layer with an HTTP/WebSocket API.

* :mod:`pyGCxGC.app.session` holds all document state and needs only the core
  dependencies.
* :mod:`pyGCxGC.app.server` exposes a session over HTTP (needs the ``app``
  extra: ``pip install "pyGCxGC[app]"``). Start the app with ``pygcxgc`` or
  ``python -m pyGCxGC.app``.
* :mod:`pyGCxGC.app.viewer` runs the app in a notebook kernel on live objects
  (``gcgc.view(obj)``).
* :mod:`pyGCxGC.app.paths` knows where settings, caches and logs are kept.

A saved project (``*.gcxgc``) opens with ``Session.load(path)``.
"""

from __future__ import annotations

from .session import InvalidRequest, Job, NotFound, Session, SessionError

__all__ = ["InvalidRequest", "Job", "NotFound", "Session", "SessionError"]
