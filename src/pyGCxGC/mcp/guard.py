"""
Guards of the MCP server (spec mcp.md §10): read and write roots, output
limits and the redaction of licensed library content.

Everything a tool returns goes to the model provider, so the guards are
enforced here, in the server, whatever the client asks or allows (SAFE-6):

* :class:`Roots` refuses paths outside the read roots (SAFE-1) and confines
  writes to the output folder, without overwriting (SAFE-2).
* :func:`redact_hit` and :func:`redact_rows` drop what a library that is not
  redistributable must not reveal: NIST numbers, library positions,
  structures and peak lists (SAFE-3). Only hit metadata stays: name, formula,
  MW, CAS, the match values and the class.
* :func:`fit` keeps results small (TOOL-3): larger ones go to a file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

MAX_TEXT = 8000  # characters of JSON per tool result (TOOL-3)
MAX_ROWS = 200

# hit fields that may leave the machine for any library (SAFE-3)
HIT_FIELDS = (
    "rank",
    "name",
    "formula",
    "mw",
    "cas",
    "match",
    "reverse_match",
    "delta_match",
    "probability",
    "library",
    "class",
    "subclass",
    "redistributable",
)
# internal or licensed fields, dropped for libraries that are not redistributable
LIBRARY_ONLY = ("nist_no", "db_no", "index", "library_id", "smiles", "inchi")


class GuardError(Exception):
    """A path or an output the server refuses (shown to the agent)."""


class Roots:
    """
    Where the server may read and write.

    Parameters
    ----------
    read : iterable of path
        Folders (and single files) below which files may be read: the
        working directory at start, the files given on the command line and
        the ``--allow-read`` folders.
    output : path
        The only folder written to, created on the first write.
    extra : callable, optional
        More read roots, asked at every check (the configured libraries,
        which change while the server runs).
    cwd : path, optional
        Base of relative paths (default: the working directory at start).
    """

    def __init__(
        self,
        read: Iterable[str | Path],
        output: str | Path,
        extra: Callable[[], Iterable[str | Path]] | None = None,
        cwd: str | Path | None = None,
    ):
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.output = self._absolute(output).resolve()
        self._read = [self._absolute(p).resolve() for p in read]
        self._extra = extra

    def _absolute(self, path: str | Path) -> Path:
        p = Path(path).expanduser()
        return p if p.is_absolute() else self.cwd / p

    @property
    def read_roots(self) -> list[Path]:
        roots = [*self._read, self.output]
        if self._extra is not None:
            roots += [Path(p).expanduser().resolve() for p in self._extra() if p]
        return list(dict.fromkeys(roots))

    def allow(self, path: str | Path) -> None:
        """Add a read root (e.g. a file given on the command line)."""
        p = self._absolute(path).resolve()
        if p not in self._read:
            self._read.append(p)

    def check_read(self, path: str | Path) -> Path:
        """The resolved path (symlinks followed) if it lies below a read root."""
        if not str(path).strip():
            raise GuardError("Give a path")
        resolved = self._absolute(path).resolve()
        roots = self.read_roots
        if any(resolved == r or resolved.is_relative_to(r) for r in roots):
            return resolved
        shown = ", ".join(str(r) for r in roots)
        raise GuardError(
            f"{path} is outside the folders this server may read ({shown}). "
            "The user can add a folder with --allow-read when starting the server."
        )

    def output_path(self, filename: str, overwrite: bool = False) -> Path:
        """
        A path in the output folder for ``filename`` (a relative name
        without ``..``); refuses existing files unless ``overwrite``.
        """
        name = str(filename or "").strip()
        rel = Path(name)
        if not name or rel.is_absolute() or rel.drive or ".." in rel.parts:
            raise GuardError(
                f"{filename!r} is not allowed: give a relative file name without "
                f"'..'; files are written to the output folder {self.output}"
            )
        target = (self.output / rel).resolve()
        if not target.is_relative_to(self.output):
            raise GuardError(f"{filename!r} leads outside the output folder")
        if target.exists() and not overwrite:
            raise GuardError(
                f"{target.relative_to(self.output)} exists in the output folder; "
                "pass overwrite=true or choose another name"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


# ------------------------------------------------------------------ redaction
def redact_hit(hit: dict, redistributable: bool | None = None) -> dict:
    """
    A hit as the agent may see it (SAFE-3). ``redistributable`` defaults to
    the hit's own ``redistributable`` field (set by the session).
    """
    if redistributable is None:
        redistributable = bool(hit.get("redistributable"))
    if redistributable:
        return {k: v for k, v in hit.items() if k not in ("index", "library_id")}
    return {k: hit[k] for k in HIT_FIELDS if k in hit}


def redact_rows(rows: list[dict], redistributable_libraries: set[str]) -> list[dict]:
    """Peak rows without the library-only fields of non-redistributable hits."""
    out = []
    for row in rows:
        if row.get("library") in redistributable_libraries:
            out.append(row)
        else:
            out.append({k: v for k, v in row.items() if k not in LIBRARY_ONLY})
    return out


# ------------------------------------------------------------------ size
def jsonable(value: Any) -> Any:
    """Plain JSON types (NumPy scalars, NaN -> None, tuples -> lists)."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(v) for v in value]
    if hasattr(value, "item") and callable(value.item):  # NumPy scalar
        value = value.item()
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def fit(data: dict, roots: Roots, name: str) -> dict:
    """
    ``data`` if its JSON stays below :data:`MAX_TEXT`; otherwise the full
    result is written to the output folder and a short note returned.
    """
    text = dumps(data)
    if len(text) <= MAX_TEXT:
        return data
    k = 1
    while (roots.output / f"{name}-{k}.json").exists():
        k += 1
    path = roots.output_path(f"{name}-{k}.json")
    path.write_text(json.dumps(data, indent=1, default=str), "utf-8")
    small = {
        key: value
        for key, value in data.items()
        if not isinstance(value, dict | list) or len(dumps(value)) < 400
    }
    return {
        **small,
        "written_to": str(path),
        "note": f"The full result ({len(text) // 1000} kB) is in {path.name} in the "
        "output folder; narrow the request (limit, filters, ranges) to see it here",
    }


__all__ = [
    "HIT_FIELDS",
    "LIBRARY_ONLY",
    "MAX_ROWS",
    "MAX_TEXT",
    "GuardError",
    "Roots",
    "dumps",
    "fit",
    "jsonable",
    "redact_hit",
    "redact_rows",
]
