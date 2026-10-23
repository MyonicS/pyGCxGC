"""Tests for pyGCxGC.masks (vector mask sets)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile
from matplotlib.path import Path as MplPath
from synthetic import synthetic_run

import pyGCxGC as gcgc
from pyGCxGC.masks import Grid, MaskSet, polygon_mask, simplify_lasso, simplify_polygon
from pyGCxGC.ms import GCxGC_MS

EXAMPLE = Path(__file__).parents[1] / "example_data"
FID_CSV = EXAMPLE / "example_chromatograms" / "Example_FID.csv"
MASK_DIR = EXAMPLE / "example_masks"

# 10 modulations of 4 s, 0.5 s sampling, starting at RT1 = 1 min
GRID = Grid(
    n_rt2=8, n_mod=10, rt1_start=1.0, modulation_time=4.0, sampling_interval=0.5
)


@pytest.fixture(scope="module")
def fid():
    return gcgc.parse_2D_chromatogram(FID_CSV, modulation_time=20)


def test_grid_geometry():
    np.testing.assert_allclose(GRID.rt1, 1.0 + np.arange(10) * 4 / 60)
    np.testing.assert_allclose(GRID.rt2, np.arange(8) * 0.5)
    np.testing.assert_allclose(GRID.rt1_centres - GRID.rt1, 2 / 60)
    np.testing.assert_allclose(GRID.rt2_centres - GRID.rt2, 0.25)
    assert Grid.from_dict(GRID.to_dict()) == GRID


def test_grid_from_chromatograms(fid):
    g = Grid.from_chromatogram(fid)
    assert g.shape == fid.chrom_2D.shape == (5000, 317)
    assert (g.rt1_start, g.modulation_time, g.sampling_interval) == (0.0, 20.0, 0.004)
    raw, _ = synthetic_run()
    ms = GCxGC_MS(raw, 4.0, shift=3)
    g = Grid.from_chromatogram(ms)
    assert g.shape == ms.tic_2d.shape
    np.testing.assert_allclose(g.rt1, ms.rt1)
    np.testing.assert_allclose(g.rt2, ms.rt2)


def test_rect_uses_pixel_centres():
    masks = MaskSet()
    m = masks.new_mask("A")
    # column centres are 1.0333 + 0.0667 j min, row centres 0.25 + 0.5 i s:
    # columns 2-4 (1.1667, 1.2333, 1.3) and rows 1-2 (0.75, 1.25)
    m.add_rect(rt1=(1.13, 1.31), rt2=(0.6, 1.3))
    w = m.rasterize(GRID, origin="lower")
    rows, cols = np.nonzero(w)
    assert sorted(set(rows)) == [1, 2]
    assert sorted(set(cols)) == [2, 3, 4]
    # "upper" is the chrom_2D/TIFF layout: row 0 is the highest RT2
    np.testing.assert_array_equal(m.rasterize(GRID), w[::-1])


def test_polygon_even_odd_like_matplotlib():
    rng = np.random.default_rng(1)
    xc = np.sort(rng.uniform(0, 10, 200))
    yc = np.sort(rng.uniform(0, 10, 150))
    xx, yy = np.meshgrid(xc, yc)
    points = np.c_[xx.ravel(), yy.ravel()]
    for _ in range(30):
        v = rng.uniform(0, 10, (rng.integers(3, 30), 2))
        expected = MplPath(v).contains_points(points).reshape(len(yc), len(xc))
        np.testing.assert_array_equal(polygon_mask(v, xc, yc), expected)
    # pentagram: the centre is outside under the even-odd rule
    angle = np.pi / 2 + np.arange(5) * 4 * np.pi / 5
    star = np.c_[5 + 4 * np.cos(angle), 5 + 4 * np.sin(angle)]
    inside = polygon_mask(star, np.array([5.0]), np.array([5.0, 8.0]))
    assert inside[:, 0].tolist() == [False, True]


def test_add_subtract_order():
    masks = MaskSet()
    m = masks.new_mask("A")
    m.add_rect((1.0, 2.0), (0, 4))
    m.subtract_polygon([(1.1, 0.5), (1.3, 0.5), (1.3, 2.0), (1.1, 2.0)])
    w = m.rasterize(GRID, "lower")
    assert w[0, 0] == 1
    assert w[2, 3] == 0  # centre (1.2333, 1.25) is in the subtracted polygon
    m.add_rect((1.2, 1.25), (1.0, 1.5))  # later operations win
    assert m.rasterize(GRID, "lower")[2, 3] == 1


def test_example_masks_integrate_exactly(fid):
    expected = gcgc.integrate_masks(fid.chrom_2D, MASK_DIR)
    masks = MaskSet.from_tiffs(MASK_DIR, fid)
    assert masks.integrate(fid) == {k: float(v) for k, v in expected.items()}
    table = masks.integration(fid)
    assert table.loc["Mask_1", "volume"] == expected["Mask_1"]
    union = np.maximum(*masks.rasterize(fid).values())
    total = table.attrs["total"]
    assert table.loc["unassigned", "volume"] == pytest.approx(
        total - gcgc.integrate_2D(fid.chrom_2D * union)
    )
    assert table.loc["Mask_1", "percent"] == pytest.approx(
        expected["Mask_1"] / total * 100
    )
    assert masks.overlaps(fid) == [("Mask_1", "Mask_2", 625)]


def test_tiff_round_trip_bit_for_bit(fid, tmp_path):
    masks = MaskSet.from_tiffs(MASK_DIR, fid)
    drawn = masks.new_mask("Drawn")
    drawn.add_rect((20, 40), (5, 10))
    files = masks.to_tiffs(tmp_path, fid)
    for name in ("Mask_1", "Mask_2"):
        np.testing.assert_array_equal(
            tifffile.imread(tmp_path / f"{name}.tif"),
            tifffile.imread(MASK_DIR / f"{name}.tif"),
        )
    drawn_tiff = tifffile.imread(tmp_path / "Drawn.tif")
    assert drawn_tiff.dtype == np.uint8
    assert set(np.unique(drawn_tiff)) == {0, 255}
    assert drawn_tiff.shape == fid.chrom_2D.shape
    # exported TIFFs work with the existing functions
    expected = gcgc.integrate_masks(fid.chrom_2D, [str(f) for f in files])
    assert masks.integrate(fid) == {k: float(v) for k, v in expected.items()}
    back = MaskSet.from_tiffs(tmp_path, fid)
    for name in masks.names:
        np.testing.assert_array_equal(
            back[name].rasterize(fid), masks[name].rasterize(fid)
        )


def test_json_round_trip(fid, tmp_path):
    masks = MaskSet.from_tiffs(MASK_DIR, fid)
    m = masks.new_mask("Aromatics", color="#123456", category="monoaromatics")
    m.add_polygon([(10, 2), (30, 2), (30, 9)])
    m.subtract_rect((15, 20), (3, 4))
    path = masks.save(tmp_path / "set.gcxgc-masks.json")
    back = MaskSet.load(path)
    assert back.names == masks.names
    assert back.grid == Grid.from_chromatogram(fid)
    assert back["Aromatics"].category == "monoaromatics"
    assert back["Aromatics"].color == "#123456"
    assert [op.id for op in back["Aromatics"].operations] == [
        op.id for op in m.operations
    ]
    assert back.integrate(fid) == masks.integrate(fid)


def test_vector_masks_transfer_between_grids(fid):
    masks = MaskSet()
    masks.new_mask("Box").add_rect((20, 40), (5, 10))
    coarse = Grid(
        n_rt2=500, n_mod=317, rt1_start=0, modulation_time=20, sampling_interval=0.04
    )
    fine = Grid.from_chromatogram(fid)
    a = masks["Box"].rasterize(coarse, "lower")
    b = masks["Box"].rasterize(fine, "lower")
    assert a.sum() * 10 == pytest.approx(b.sum(), rel=0.01)


def test_raster_mask_needs_matching_shape(fid):
    masks = MaskSet.from_tiffs(MASK_DIR, fid)
    with pytest.raises(ValueError, match="does not match the chromatogram"):
        masks.rasterize(GRID)
    with pytest.raises(ValueError, match="has shape"):
        MaskSet().add_tiff(MASK_DIR / "Mask_1.tif", GRID)


def test_mask_set_editing():
    masks = MaskSet()
    a = masks.new_mask()
    b = masks.new_mask()
    assert masks.names == ["Mask 1", "Mask 2"]
    assert a.color != b.color
    copy = masks.duplicate("Mask 1")
    assert masks.names == ["Mask 1", "Mask 1 copy", "Mask 2"]
    assert copy.id != a.id
    assert masks.new_mask("Mask 2").name == "Mask 2 (2)"
    with pytest.raises(ValueError, match="already exists"):
        masks.rename(copy.id, "Mask 2")
    masks.rename(copy.id, "Renamed")
    masks.move("Renamed", 0)
    assert masks.names[0] == "Renamed"
    masks.remove(a.id)
    assert "Mask 1" not in masks
    snapshot = masks.copy()
    masks["Mask 2"].add_rect((1, 2), (0, 1))
    assert not snapshot["Mask 2"].operations


def test_simplify_lasso():
    angle = np.linspace(0, 2 * np.pi, 1000, endpoint=False)
    circle = np.c_[1.5 + 0.2 * np.cos(angle), 2.0 + 1.5 * np.sin(angle)]
    simple = simplify_lasso(circle, GRID)
    assert 3 <= len(simple) < 100
    masks = MaskSet()
    masks.new_mask("full").add_polygon(circle)
    masks.new_mask("simple").add_polygon(simple)
    w = masks.rasterize(GRID)
    assert np.abs(w["full"] - w["simple"]).sum() <= 2
    square = [(0, 0), (1, 0), (1, 1), (0.5, 1.0001), (0, 1)]
    assert len(simplify_polygon(square, 0.01)) == 4
