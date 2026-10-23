"""
Binary encoding of arrays for the app API.

Arrays are never sent as JSON numbers. A body is::

    uint32 (little-endian)   n = length of the header in bytes
    n bytes                  UTF-8 JSON header, padded with spaces
    data                     raw little-endian array data (C order)

The header is padded so that the data starts at a multiple of 8 bytes, so the
client can view it without copying (``new Float32Array(buffer, 4 + n)``).
The header always contains ``shape`` and ``dtype`` (``float32``,
``float64``, ``uint8``, ``int32``, ...) plus any metadata, e.g. the
``rt1``/``rt2``/``P``/``dt`` geometry of images.
"""

from __future__ import annotations

import json
import struct

import numpy as np

MEDIA_TYPE = "application/vnd.pygcxgc.array"
DTYPES = ("float32", "float64", "uint8", "uint16", "int32", "uint32")
_ALIGN = 8


def encode_array(array: np.ndarray, header: dict | None = None) -> bytes:
    """
    Encode an array with a JSON header.

    Parameters
    ----------
    array : np.ndarray
        Array with one of the dtypes in :data:`DTYPES`.
    header : dict, optional
        Extra metadata (``shape`` and ``dtype`` are added).
    """
    array = np.asarray(array)
    if array.dtype.name not in DTYPES:
        raise ValueError(f"Unsupported dtype {array.dtype} (use one of {DTYPES})")
    data = np.ascontiguousarray(array, dtype=array.dtype.newbyteorder("<"))
    meta = {**(header or {}), "shape": list(array.shape), "dtype": array.dtype.name}
    text = json.dumps(meta, separators=(",", ":"), allow_nan=False).encode("utf-8")
    text += b" " * (-(4 + len(text)) % _ALIGN)
    return struct.pack("<I", len(text)) + text + data.tobytes()


def decode_array(body: bytes) -> tuple[dict, np.ndarray]:
    """Inverse of :func:`encode_array`: ``(header, array)``."""
    (n,) = struct.unpack_from("<I", body, 0)
    header = json.loads(body[4 : 4 + n].decode("utf-8"))
    dtype = np.dtype(header["dtype"]).newbyteorder("<")
    offset = 4 + n
    offset += -offset % _ALIGN
    array = np.frombuffer(body, dtype=dtype, offset=offset).reshape(header["shape"])
    return header, array


def max_pool(
    image: np.ndarray, max_rows: int, max_cols: int
) -> tuple[np.ndarray, int, int]:
    """
    Downsample an image by max pooling so that it fits ``max_rows x max_cols``.

    Returns
    -------
    pooled, row_factor, col_factor
        A pooled pixel covers ``row_factor x col_factor`` original pixels
        (the last block may be partial).
    """
    rows, cols = image.shape
    fr = max(1, -(-rows // max(max_rows, 1)))
    fc = max(1, -(-cols // max(max_cols, 1)))
    if fr == 1 and fc == 1:
        return image, 1, 1
    pr, pc = -rows % fr, -cols % fc
    if np.issubdtype(image.dtype, np.floating):
        fill = -np.inf
    else:
        fill = np.iinfo(image.dtype).min
    padded = np.pad(image, ((0, pr), (0, pc)), constant_values=fill)
    pooled = padded.reshape(padded.shape[0] // fr, fr, padded.shape[1] // fc, fc)
    return pooled.max(axis=(1, 3)).astype(image.dtype), fr, fc
