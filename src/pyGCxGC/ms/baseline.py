"""
Baseline (background) models for GCxGC-MS data.

GC-MS data contain a per-ion background (detector offset, column bleed, air)
that drifts over the run. A baseline model estimates the background spectrum
of every scan, which :class:`~pyGCxGC.ms.data.GCxGC_MS` subtracts from the 2D
image, extracted ion chromatograms and spectra.

Models
------
``none``
    Raw intensities, no channel zeroing.
``modulation_percentile`` (default)
    Per ion, a low percentile of the ion within its modulation. Noise is the
    scaled median absolute deviation (MAD) within the modulation.
``rolling_percentile``
    Like ``modulation_percentile``, but the percentile is taken over the
    modulation and ``window`` neighbouring modulations on each side. A broad
    compound that fills one modulation then disturbs the baseline less.
``local`` (spectra only)
    The mean of the scans on both sides of the selected point in the same
    modulation, ``gap`` to ``gap + width`` scans away.
``reference`` (spectra only)
    The mean spectrum of ``n_scans`` scans around a user-picked background
    scan.

The spectra-only models use ``image_model`` (default
``modulation_percentile``) for the image, the EICs and peak identification.
Channels below ``snr * noise`` are zeroed only for the percentile models (see
:meth:`GCxGC_MS.spectrum`).

Examples
--------
>>> import pyGCxGC as gcgc
>>> chrom = gcgc.read_gcxgc_ms(
...     "run.cdf", 20, baseline={"mode": "rolling_percentile", "window": 2}
... )  # doctest: +SKIP
>>> chrom.set_baseline("local", gap=3, width=5)  # doctest: +SKIP
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, ClassVar

import numpy as np

if TYPE_CHECKING:
    from pyGCxGC.ms.data import GCxGC_MS


class BaselineModel:
    """
    Base class of the baseline models.

    A model holds its parameters; :meth:`fit` estimates the background of a
    :class:`~pyGCxGC.ms.data.GCxGC_MS`, after which :meth:`baseline_of_scans`
    returns the baseline spectrum of any scan.

    Attributes
    ----------
    mode : str
        Name of the model (key in :data:`BASELINES`).
    applies_to_image : bool
        ``False`` for models that only change spectra; these delegate the image
        baseline to :attr:`image_model`.
    """

    mode: ClassVar[str] = ""
    applies_to_image: ClassVar[bool] = True

    def params(self) -> dict:
        """Parameters of the model (JSON serialisable)."""
        return {}

    def to_dict(self) -> dict:
        """``{"mode": ..., **params}``, accepted by :func:`make_baseline`."""
        return {"mode": self.mode, **self.params()}

    def describe(self) -> str:
        """Short description, e.g. ``modulation_percentile(percentile=25)``."""
        args = ", ".join(
            f"{k}={_fmt(v)}" for k, v in self.params().items() if k != "image_model"
        )
        return f"{self.mode}({args})"

    def fresh(self) -> BaselineModel:
        """Unfitted copy with the same parameters."""
        return make_baseline(self.to_dict())

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.describe()}>"

    def __eq__(self, other) -> bool:
        return isinstance(other, BaselineModel) and self.to_dict() == other.to_dict()

    __hash__ = None  # type: ignore[assignment]

    @property
    def image_model(self) -> BaselineModel:
        """Model used for the image, EICs and peak spectra."""
        return self

    # ------------------------------------------------------------ interface
    def fit(self, data: GCxGC_MS) -> None:
        """Estimate the background of ``data``."""
        raise NotImplementedError

    def baseline_of_scans(self, scans) -> np.ndarray:
        """Baseline spectra (``(len(scans), n_mz)``) of the given scans."""
        raise NotImplementedError

    def summed_baseline(self, columns=None) -> np.ndarray:
        """
        Baseline of every scan, summed over m/z channels.

        Parameters
        ----------
        columns : array-like of int, optional
            Channel (column) indices to sum. All channels by default.
        """
        raise NotImplementedError

    def noise_of_scans(self, scans) -> np.ndarray | None:  # noqa: ARG002
        """Noise spectra of the scans, or ``None`` when channels are not zeroed."""
        return None

    def spectrum_baseline(self, data: GCxGC_MS, scans: np.ndarray) -> np.ndarray:  # noqa: ARG002
        """Baseline of the sum of ``scans`` (one value per m/z channel)."""
        return self.baseline_of_scans(scans).sum(axis=0)


class NoBaseline(BaselineModel):
    """No baseline correction and no channel zeroing."""

    mode = "none"

    def fit(self, data: GCxGC_MS) -> None:
        self._n_mz = data.raw.intensities.shape[1]
        self._n_scans = data.n_scans

    def baseline_of_scans(self, scans) -> np.ndarray:
        return np.zeros((len(np.atleast_1d(scans)), self._n_mz), dtype=np.float32)

    def summed_baseline(self, columns=None) -> np.ndarray:  # noqa: ARG002
        return np.zeros(self._n_scans, dtype=np.float32)


class ModulationPercentile(BaselineModel):
    """
    Per ion percentile within each modulation (the default model).

    Parameters
    ----------
    percentile : float, default 25
        Percentile of every ion that is used as its baseline.
    """

    mode = "modulation_percentile"

    def __init__(self, percentile: float = 25.0):
        if not 0 <= percentile <= 100:
            raise ValueError("percentile must be between 0 and 100")
        self.percentile = float(percentile)

    def params(self) -> dict:
        return {"percentile": self.percentile}

    def _window(self) -> int:
        """Neighbouring modulations on each side included in the estimate."""
        return 0

    def fit(self, data: GCxGC_MS) -> None:
        n = data.n_rt2
        shift = data.shift
        intensities = data.raw.intensities
        n_mod = data.n_modulations
        n_scans = data.n_scans
        k = self._window()

        def estimate(i: int):
            lo = max((i - k) * n + shift, 0)
            hi = min((i + k + 1) * n + shift, n_scans)
            if hi - lo < 3:
                return None
            block = intensities[lo:hi]
            med = np.median(block, axis=0)
            mad = np.median(np.abs(block - med), axis=0)
            return np.percentile(block, self.percentile, axis=0), 1.4826 * mad

        # numpy releases the GIL while partitioning, so threads scale well
        workers = min(8, os.cpu_count() or 1)
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(estimate, range(n_mod)))
        base = np.zeros((n_mod, intensities.shape[1]), dtype=np.float32)
        noise = np.zeros_like(base)
        for i, result in enumerate(results):
            if result is None:
                if i:
                    base[i], noise[i] = base[i - 1], noise[i - 1]
                continue
            base[i], noise[i] = result
        # avoid zero noise for channels that are always 0
        floor = np.maximum(
            np.median(noise[noise > 0]) * 0.1 if np.any(noise > 0) else 1.0, 1.0
        )
        self.levels = base
        self.noise = np.maximum(noise, floor)
        self._n_rt2, self._shift, self._n_mod = n, shift, n_mod
        self._scan_mods = self.modulation_of_scans(np.arange(n_scans))

    def modulation_of_scans(self, scans) -> np.ndarray:
        """Modulation (row of :attr:`levels`) used for each scan."""
        return np.clip(
            (np.asarray(scans) - self._shift) // self._n_rt2, 0, self._n_mod - 1
        )

    def baseline_of_scans(self, scans) -> np.ndarray:
        return self.levels[self.modulation_of_scans(scans)]

    def summed_baseline(self, columns=None) -> np.ndarray:
        levels = self.levels if columns is None else self.levels[:, columns]
        return levels.sum(axis=1)[self._scan_mods]

    def noise_of_scans(self, scans) -> np.ndarray:
        return self.noise[self.modulation_of_scans(scans)]


class RollingPercentile(ModulationPercentile):
    """
    Per ion percentile over neighbouring modulations.

    Parameters
    ----------
    percentile : float, default 25
        Percentile of every ion that is used as its baseline.
    window : int, default 2
        Number of modulations on each side that are included.
    """

    mode = "rolling_percentile"

    def __init__(self, percentile: float = 25.0, window: int = 2):
        super().__init__(percentile)
        if window < 0:
            raise ValueError("window must not be negative")
        self.window = int(window)

    def params(self) -> dict:
        return {"percentile": self.percentile, "window": self.window}

    def _window(self) -> int:
        return self.window


class _SpectrumOnly(BaselineModel):
    """Model that changes spectra only; the image uses ``image_model``."""

    applies_to_image = False

    def __init__(self, image_model=None):
        model = make_baseline(image_model)
        if not model.applies_to_image:
            raise ValueError("image_model must be a model that applies to the image")
        self._image_model = model

    @property
    def image_model(self) -> BaselineModel:
        return self._image_model

    def params(self) -> dict:
        return {"image_model": self._image_model.to_dict()}

    def describe(self) -> str:
        return f"{super().describe()}; image: {self._image_model.describe()}"

    def fit(self, data: GCxGC_MS) -> None:
        self._image_model.fit(data)

    def baseline_of_scans(self, scans) -> np.ndarray:
        return self._image_model.baseline_of_scans(scans)

    def summed_baseline(self, columns=None) -> np.ndarray:
        return self._image_model.summed_baseline(columns)


class LocalBaseline(_SpectrumOnly):
    """
    Background from scans on both sides of the selected point (spectra only).

    Parameters
    ----------
    gap : int, default 3
        Distance (scans) between the selected scan and the first background
        scan.
    width : int, default 5
        Number of background scans on each side.
    image_model : BaselineModel, str or dict, optional
        Model used for the image (default ``modulation_percentile``).
    """

    mode = "local"

    def __init__(self, gap: int = 3, width: int = 5, image_model=None):
        super().__init__(image_model)
        if gap < 1 or width < 1:
            raise ValueError("gap and width must be at least 1")
        self.gap = int(gap)
        self.width = int(width)

    def params(self) -> dict:
        return {"gap": self.gap, "width": self.width, **super().params()}

    def background_scans(self, data: GCxGC_MS, scans: np.ndarray) -> np.ndarray:
        """Background scans used for the sum of ``scans``."""
        scans = np.atleast_1d(scans)
        centre = int(scans[len(scans) // 2])
        _, mod = data.pixel_of_scan(centre)
        first = max(mod * data.n_rt2 + data.shift, 0)
        last = min((mod + 1) * data.n_rt2 + data.shift, data.n_scans) - 1
        offsets = np.arange(self.gap, self.gap + self.width)
        candidates = np.concatenate([centre - offsets[::-1], centre + offsets])
        keep = (candidates >= first) & (candidates <= last)
        candidates = candidates[keep]
        return candidates[~np.isin(candidates, scans)]

    def spectrum_baseline(self, data: GCxGC_MS, scans: np.ndarray) -> np.ndarray:
        background = self.background_scans(data, scans)
        if len(background) == 0:
            return np.zeros(data.raw.intensities.shape[1])
        mean = data.raw.intensities[background].astype(np.float64).mean(axis=0)
        return mean * len(np.atleast_1d(scans))


class ReferenceBaseline(_SpectrumOnly):
    """
    Background from a user-picked point (spectra only).

    Parameters
    ----------
    scan : int
        Centre scan of the background region.
    n_scans : int, default 3
        Number of scans around ``scan`` that are averaged.
    image_model : BaselineModel, str or dict, optional
        Model used for the image (default ``modulation_percentile``).
    """

    mode = "reference"

    def __init__(self, scan: int, n_scans: int = 3, image_model=None):
        super().__init__(image_model)
        if n_scans < 1:
            raise ValueError("n_scans must be at least 1")
        self.scan = int(scan)
        self.n_scans = int(n_scans)

    def params(self) -> dict:
        return {"scan": self.scan, "n_scans": self.n_scans, **super().params()}

    def fit(self, data: GCxGC_MS) -> None:
        super().fit(data)
        if not 0 <= self.scan < data.n_scans:
            raise ValueError(f"Reference scan {self.scan} is outside the data")
        lo = max(self.scan - self.n_scans // 2, 0)
        hi = min(lo + self.n_scans, data.n_scans)
        self.reference = data.raw.intensities[lo:hi].astype(np.float64).mean(axis=0)

    def spectrum_baseline(self, data: GCxGC_MS, scans: np.ndarray) -> np.ndarray:  # noqa: ARG002
        return self.reference * len(np.atleast_1d(scans))


BASELINES: dict[str, type[BaselineModel]] = {
    cls.mode: cls
    for cls in (
        NoBaseline,
        ModulationPercentile,
        RollingPercentile,
        LocalBaseline,
        ReferenceBaseline,
    )
}


def make_baseline(spec=None, **params) -> BaselineModel:
    """
    Create an (unfitted) baseline model.

    Parameters
    ----------
    spec : BaselineModel, str or dict, optional
        A model (an unfitted copy is returned), a mode name from
        :data:`BASELINES`, or a dict ``{"mode": name, **params}``. ``None``
        gives the default ``modulation_percentile`` model.
    **params
        Parameters of the model, when ``spec`` is a name.

    Examples
    --------
    >>> make_baseline("rolling_percentile", window=3).describe()
    'rolling_percentile(percentile=25, window=3)'
    """
    if isinstance(spec, BaselineModel):
        spec = spec.to_dict()
    if spec is None:
        spec = ModulationPercentile.mode
    if isinstance(spec, dict):
        params = {**spec, **params}
        spec = params.pop("mode", ModulationPercentile.mode)
    if spec not in BASELINES:
        raise ValueError(
            f"Unknown baseline mode {spec!r}; choose from {', '.join(BASELINES)}"
        )
    return BASELINES[spec](**params)


def _fmt(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
