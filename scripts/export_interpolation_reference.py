"""
Write the interpolation reference of the frontend's shader test
(``frontend/src/chromatogram/interpolation.reference.json``).

A small random image is upsampled 8 times with matplotlib's own resampler
(``matplotlib._image.resample``, the Agg filters behind ``imshow``) for the
interpolations the app offers. The browser test renders the same image with
the WebGL shader and compares, so ``nearest``/``bilinear``/``bicubic`` in the
app match matplotlib's results under the same names.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import numpy as np
from matplotlib import _image
from matplotlib.transforms import Affine2D

OUT = (
    Path(__file__).parents[1]
    / "frontend"
    / "src"
    / "chromatogram"
    / "interpolation.reference.json"
)
SHAPE = (5, 6)
FACTOR = 8


def export(path: Path = OUT) -> dict:
    src = np.round(np.random.default_rng(0).random(SHAPE), 4)
    data = {
        "source": f"matplotlib {mpl.__version__} _image.resample",
        "factor": FACTOR,
        "shape": list(SHAPE),
        "image": src.ravel().tolist(),
        "resampled": {},
    }
    for name in ("nearest", "bilinear", "bicubic"):
        out = np.zeros((SHAPE[0] * FACTOR, SHAPE[1] * FACTOR))
        method = getattr(_image, name.upper())
        _image.resample(src, out, Affine2D().scale(FACTOR), method, True, 1.0, 1.0)
        data["resampled"][name] = np.round(out, 5).ravel().tolist()
    path.write_text(json.dumps(data) + "\n", "utf-8")
    return data


if __name__ == "__main__":
    export()
