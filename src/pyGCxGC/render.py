"""
PNG renders of chromatograms for agents and reports (spec RND-1…5).

:func:`render_chromatogram` draws a 2D chromatogram with readable axes
(RT1 in min, RT2 in s, gridlines dense enough to read a position to about
1 % of the shown range), mask outlines and peak markers, and returns the PNG
with a caption that says what was drawn, so a model without image input
still learns it. The output is deterministic: the same input gives the same
bytes.

Publication figures are made in the app (``lib/figure.ts``); these renders
are for agents and quick looks.

Examples
--------
>>> png, caption = render_chromatogram(
...     chrom, masks=masks, peaks=table
... )  # doctest: +SKIP
>>> Path("overview.png").write_bytes(png)  # doctest: +SKIP
>>> print(caption["text"])  # doctest: +SKIP
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import numpy as np

from pyGCxGC.masks import Grid

if TYPE_CHECKING:
    import pandas as pd

    from pyGCxGC.masks import MaskSet

MAX_SIDE = 2048
MAX_LABELS = 40
SCALES = ("sqrt", "log", "linear")
_FALLBACK_COLORS = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#bcbd22",
    "#17becf",
)


def default_image(chrom) -> np.ndarray:
    """The TIC (MS) or processed signal (FID) in the ``origin="lower"`` layout."""
    if hasattr(chrom, "tic_2d"):
        return np.asarray(chrom.tic_2d)
    if hasattr(chrom, "chrom_2D"):
        return np.asarray(chrom.chrom_2D.to_numpy()[::-1])
    raise TypeError("Give an image for a grid")


def _norm(scale: str, vmin: float, vmax: float):
    from matplotlib import colors

    if scale == "sqrt":
        return colors.PowerNorm(0.5, vmin=vmin, vmax=vmax, clip=True)
    if scale == "log":
        return colors.LogNorm(vmin=vmin, vmax=vmax, clip=True)
    return colors.Normalize(vmin=vmin, vmax=vmax, clip=True)


def _category_colors(categories, colors: dict[str, str] | None) -> dict[str, str]:
    out, k = {}, 0
    for c in categories:
        if colors and colors.get(c):
            out[c] = colors[c]
        elif c:
            out[c] = _FALLBACK_COLORS[k % len(_FALLBACK_COLORS)]
            k += 1
    return out


def render_chromatogram(
    source,
    image: np.ndarray | None = None,
    rt1_min: tuple[float, float] | None = None,
    rt2_s: tuple[float, float] | None = None,
    scale: str = "sqrt",
    cmap: str = "viridis",
    masks: MaskSet | list | None = None,
    peaks: pd.DataFrame | None = None,
    color_peaks_by: str | None = "category",
    peak_labels: str = "none",
    category_colors: dict[str, str] | None = None,
    width: int = 1024,
    height: int = 640,
    title: str | None = None,
    signal: str = "",
    value_label: str = "",
) -> tuple[bytes, dict[str, Any]]:
    """
    Render a 2D chromatogram as PNG (spec RND-1/2/3/5).

    Parameters
    ----------
    source : GCxGC_MS, GCxGC_FID or Grid
        The chromatogram (or its grid, with ``image``).
    image : np.ndarray, optional
        ``(n_rt2, n_mod)`` signal, ``origin="lower"`` (default: the TIC or the
        FID signal).
    rt1_min, rt2_s : (min, max), optional
        Zoom ranges (min and s). Default: the whole run.
    scale : {"sqrt", "log", "linear"}
        Colour scaling; the range is clipped at the 99.5th percentile of the
        shown part.
    cmap : str
        A matplotlib colour map.
    masks : MaskSet or list of Mask, optional
        Visible masks are outlined in their colours and labelled.
    peaks : pandas.DataFrame, optional
        Peak table (``rt1_min``, ``rt2_s``; ``category``, ``match``,
        ``volume``, ``peak_id``, ``name`` when used).
    color_peaks_by : {"category", "match", None}
    peak_labels : {"none", "id", "name"}
        Label at most 40 peaks (the largest by volume in view).
    category_colors : dict, optional
        Class name -> colour (e.g. of the class scheme).
    width, height : int
        Pixels (at most 2048 on the long side).
    title, signal, value_label : str, optional
        Shown in the title and the caption.

    Returns
    -------
    png : bytes
    caption : dict
        ``text`` (a few lines) and the details: ``rt1_min``, ``rt2_s``,
        ``scale``, ``color_range``, ``signal``, ``masks`` (name, colour),
        ``peaks`` (number per category in view), ``labelled`` peaks.
    """
    # a Figure with its own Agg canvas: no pyplot, the user's backend stays
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import AutoMinorLocator, MaxNLocator

    if scale not in SCALES:
        raise ValueError(f"Unknown scale {scale!r}; use {', '.join(SCALES)}")
    if peak_labels not in ("none", "id", "name"):
        raise ValueError("peak_labels must be 'none', 'id' or 'name'")
    if color_peaks_by not in ("category", "match", None, "none"):
        raise ValueError("color_peaks_by must be 'category', 'match' or None")
    grid = Grid.from_chromatogram(source)
    data = default_image(source) if image is None else np.asarray(image)
    if data.shape != grid.shape:
        raise ValueError(f"The image has shape {data.shape}, the grid {grid.shape}")
    width, height = int(width), int(height)
    if min(width, height) < 64:
        raise ValueError("The image must be at least 64 pixels wide and high")
    factor = min(1.0, MAX_SIDE / max(width, height))
    width, height = round(width * factor), round(height * factor)

    x0, x1 = grid.rt1_start, grid.rt1_start + grid.n_mod * grid.modulation_time / 60.0
    y0, y1 = grid.rt2_start, grid.rt2_start + grid.n_rt2 * grid.sampling_interval
    xr = (max(rt1_min[0], x0), min(rt1_min[1], x1)) if rt1_min else (x0, x1)
    yr = (max(rt2_s[0], y0), min(rt2_s[1], y1)) if rt2_s else (y0, y1)
    if xr[0] >= xr[1] or yr[0] >= yr[1]:
        raise ValueError("The requested range lies outside the chromatogram")
    cols = (grid.rt1_centres >= xr[0]) & (grid.rt1_centres <= xr[1])
    rows = (grid.rt2_centres >= yr[0]) & (grid.rt2_centres <= yr[1])
    shown = np.clip(
        data[np.ix_(rows, cols)] if rows.any() and cols.any() else data, 0, None
    )
    positive = shown[shown > 0]
    vmax = float(np.percentile(positive, 99.5)) if positive.size else 1.0
    vmax = vmax if vmax > 0 else 1.0
    vmin = (
        (vmax * 1e-4 if positive.size == 0 else max(float(positive.min()), vmax * 1e-4))
        if scale == "log"
        else 0.0
    )

    dpi = 100
    fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    img = ax.imshow(
        np.clip(data, vmin if scale == "log" else 0, None),
        origin="lower",
        extent=(x0, x1, y0, y1),
        aspect="auto",
        interpolation="nearest",
        cmap=cmap,
        norm=_norm(scale, vmin, vmax),
    )
    ax.set_xlim(*xr)
    ax.set_ylim(*yr)
    ax.set_xlabel("RT1 (min)")
    ax.set_ylabel("RT2 (s)")
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_locator(MaxNLocator(nbins=10))
        axis.set_minor_locator(AutoMinorLocator(5))
    ax.grid(which="major", color="white", alpha=0.35, linewidth=0.6)
    ax.grid(which="minor", color="white", alpha=0.12, linewidth=0.4)
    ax.tick_params(which="both", direction="out", labelsize=8)
    bar = fig.colorbar(img, ax=ax, pad=0.01, fraction=0.04)
    bar.ax.tick_params(labelsize=7)
    if value_label:
        bar.set_label(value_label, fontsize=8)
    if title:
        ax.set_title(title, fontsize=9)

    drawn_masks = []
    for mask in list(masks or []):
        if not getattr(mask, "visible", True) or not mask.operations:
            continue
        try:
            raster = mask.rasterize(grid, "lower")
        except ValueError:
            continue
        if not raster.any():
            continue
        ax.contour(
            grid.rt1_centres,
            grid.rt2_centres,
            raster,
            levels=[0.5],
            colors=[mask.color],
            linewidths=1.4,
        )
        r, c = np.nonzero(raster > 0)
        lx = float(np.clip(grid.rt1_centres[c].min(), *xr))
        ly = float(np.clip(grid.rt2_centres[r].max(), *yr))
        ax.annotate(
            mask.name,
            (lx, ly),
            xytext=(2, 2),
            textcoords="offset points",
            fontsize=7,
            color=mask.color,
            bbox={"boxstyle": "round,pad=0.15", "fc": "black", "alpha": 0.5, "lw": 0},
            annotation_clip=True,
        )
        drawn_masks.append(
            {"name": mask.name, "color": mask.color, "category": mask.category}
        )

    peak_counts: dict[str, int] = {}
    labelled: list[int] = []
    if peaks is not None and len(peaks):
        p = peaks
        px = p["rt1_min"].to_numpy(float) + grid.modulation_time / 120.0
        py = p["rt2_s"].to_numpy(float) + grid.sampling_interval / 2.0
        view = (px >= xr[0]) & (px <= xr[1]) & (py >= yr[0]) & (py <= yr[1])
        p, px, py = p[view], px[view], py[view]
        cats = (
            p["category"].fillna("").astype(str).to_numpy()
            if "category" in p
            else np.array([""] * len(p))
        )
        for c in cats:
            key = c or "unlabelled"
            peak_counts[key] = peak_counts.get(key, 0) + 1
        if color_peaks_by == "category":
            palette = _category_colors(sorted(set(cats) - {""}), category_colors)
            unl = cats == ""
            if unl.any():
                ax.scatter(
                    px[unl],
                    py[unl],
                    s=14,
                    facecolors="none",
                    edgecolors="white",
                    linewidths=0.8,
                    label="unlabelled",
                )
            for c, color in palette.items():
                sel = cats == c
                ax.scatter(
                    px[sel],
                    py[sel],
                    s=16,
                    c=color,
                    edgecolors="black",
                    linewidths=0.4,
                    label=c,
                )
            if palette or unl.any():
                ax.legend(
                    fontsize=6, loc="upper right", framealpha=0.6, markerscale=0.9
                )
        elif color_peaks_by == "match" and "match" in p:
            sc = ax.scatter(
                px,
                py,
                s=16,
                c=p["match"].to_numpy(float),
                cmap="RdYlGn",
                vmin=500,
                vmax=1000,
                edgecolors="black",
                linewidths=0.4,
            )
            fig.colorbar(sc, ax=ax, pad=0.01, fraction=0.03).set_label(
                "match", fontsize=7
            )
        else:
            ax.scatter(
                px, py, s=12, facecolors="none", edgecolors="white", linewidths=0.8
            )
        if peak_labels != "none" and len(p):
            order = (
                np.argsort(-p["volume"].to_numpy(float), kind="stable")
                if "volume" in p
                else np.arange(len(p))
            )[:MAX_LABELS]
            for k in order:
                row = p.iloc[int(k)]
                text = (
                    str(int(row["peak_id"]))
                    if peak_labels == "id" or not row.get("name")
                    else str(row["name"])[:28]
                )
                ax.annotate(
                    text,
                    (px[k], py[k]),
                    xytext=(3, 3),
                    textcoords="offset points",
                    fontsize=6,
                    color="white",
                )
                if "peak_id" in p:
                    labelled.append(int(row["peak_id"]))
    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, metadata={"Software": None})
    caption = {
        "rt1_min": [round(xr[0], 4), round(xr[1], 4)],
        "rt2_s": [round(yr[0], 4), round(yr[1], 4)],
        "scale": scale,
        "color_range": [float(f"{vmin:.4g}"), float(f"{vmax:.4g}")],
        "signal": signal,
        "masks": drawn_masks,
        "peaks": peak_counts,
        "labelled": labelled,
        "size": [width, height],
    }
    caption["text"] = caption_text(caption)
    return buf.getvalue(), caption


def caption_text(c: dict) -> str:
    """The caption as a few lines of text."""
    lines = [
        f"RT1 {c['rt1_min'][0]:.3f}-{c['rt1_min'][1]:.3f} min, RT2 "
        f"{c['rt2_s'][0]:.2f}-{c['rt2_s'][1]:.2f} s; {c['signal'] or 'signal'}, "
        f"{c['scale']} scale, colour range {c['color_range'][0]:.3g}-"
        f"{c['color_range'][1]:.3g} (99.5th percentile)."
    ]
    if c["masks"]:
        lines.append(
            "Masks: "
            + ", ".join(f"{m['name']} ({m['color']})" for m in c["masks"])
            + "."
        )
    if c["peaks"]:
        lines.append(
            "Peaks in view: "
            + ", ".join(
                f"{k} {v}" for k, v in sorted(c["peaks"].items(), key=lambda kv: -kv[1])
            )
            + "."
        )
    return "\n".join(lines)


__all__ = ["caption_text", "default_image", "render_chromatogram"]
