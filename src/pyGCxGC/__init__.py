"""pyGCxGC initialization file"""

from __future__ import annotations

from importlib.metadata import version

# Load the version
try:
    __version__ = version("pyGCxGC")
except:
    __version__ = "0.1.0"  # Default version if not installed

# Import and expose key modules
from . import parsing
from . import main
from . import processing

# Explicitly import the commonly used functions from each module
from .parsing import (
    parse_2D_chromatogram,
    parse_csv,
    read_chromatogram
)

from .main import (
    split_solvent,
    add_split,
    convert_to2D,
    baseline_stridewise,
    shift_phase,
    normalize_by_volume,
    mask_integrate,
    GCxGC_FID,
    is_integer_multiple
)

from .processing import (
    mask_chromatogram,
    integrate_2D,
    integrate_masks
)

from . import ms
from .ms import GCxGC_MS, read_gcxgc_ms, load_library, search_spectrum

from . import masks
from .masks import Grid, Mask, MaskSet

# Import GUI functions only if tkinter is available
try:
    from .mask_gui import launch_mask_creator # type: ignore
except ImportError:
    # Define a placeholder function that raises a more informative error
    def launch_mask_creator():
        raise ImportError(
            "The GUI functionality requires tkinter which is not installed. "
            "Please install tkinter on your system to use this feature."
        )

def launch_ms_viewer(*args, **kwargs):
    """Launch the GCxGC-MS viewer (see :mod:`pyGCxGC.gui.ms_viewer`)."""
    from .gui.ms_viewer import launch_ms_viewer as _launch

    return _launch(*args, **kwargs)


def view(obj=None, library=None, **kwargs):
    """
    Open the pyGCxGC app on a chromatogram, inline in a notebook.

    See :func:`pyGCxGC.app.viewer.view` (needs ``pip install "pyGCxGC[app]"``).
    """
    from .app.viewer import view as _view

    return _view(obj, library, **kwargs)


def __getattr__(name):
    # ``gcgc.app`` (the app's session layer) is imported on first use
    if name == "app":
        import importlib

        return importlib.import_module(".app", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Define what symbols to export when using "from pyGCxGC import *"
__all__ = [
    # Modules
    "parsing", 
    "main",
    "processing",
    # Parsing functions
    "parse_2D_chromatogram",
    "parse_csv",
    "read_chromatogram",
    # Main functions
    "split_solvent",
    "add_split",
    "convert_to2D",
    "baseline_stridewise",
    "shift_phase",
    "normalize_by_volume",
    "mask_integrate",
    "GCxGC_FID",
    "is_integer_multiple",
    "mask_chromatogram",
    "integrate_2D",
    "integrate_masks",
    # Mass spectrometry
    "ms",
    "GCxGC_MS",
    "read_gcxgc_ms",
    "load_library",
    "search_spectrum",
    # Masks
    "masks",
    "Grid",
    "Mask",
    "MaskSet",
    # GUI functions
    "launch_mask_creator",
    "launch_ms_viewer",
    # App
    "view",
]