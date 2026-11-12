"""
The pyGCxGC MCP server (spec mcp.md): an agent interface on a
:class:`~pyGCxGC.app.session.Session`.

Start it with ``pygcxgc mcp`` (``gc-squared mcp``, ``python -m pyGCxGC.mcp``);
it needs the ``mcp`` extra (``pip install "pyGCxGC[mcp]"``). The modules are
:mod:`pyGCxGC.mcp.server` (tools, guides and the prompt),
:mod:`pyGCxGC.mcp.guard` (paths, output limits, library redaction) and
:mod:`pyGCxGC.mcp.__main__` (command line).
"""
