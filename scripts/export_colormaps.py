"""
Write the colour maps of the app (``frontend/src/colormaps.json``) from matplotlib.

Each map is 256 RGB entries, taken with ``cmap(index, bytes=True)``: the same
8-bit colours matplotlib renders, so the app and notebooks show identical
colours under the same names. Run ``python scripts/export_colormaps.py``
after changing the list below.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import numpy as np

NAMES = ("viridis", "plasma", "inferno", "magma", "cividis", "turbo", "jet", "gray")
OUT = Path(__file__).parents[1] / "frontend" / "src" / "colormaps.json"


def export(path: Path = OUT) -> dict:
    maps = {}
    for name in NAMES:
        cmap = mpl.colormaps[name].resampled(256)
        rgb = cmap(np.arange(256), bytes=True)[:, :3]
        maps[name] = rgb.tobytes().hex()
    data = {
        "source": f"matplotlib {mpl.__version__}",
        "size": 256,
        "format": "hex RGB, 256 entries per map",
        "maps": maps,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return data


if __name__ == "__main__":
    export()
