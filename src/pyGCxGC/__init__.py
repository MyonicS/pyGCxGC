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
    parse_csv
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

# Import GUI functions only if tkinter is available
try:
    from .mask_gui import launch_mask_creator
except ImportError:
    # Define a placeholder function that raises a more informative error
    def launch_mask_creator():
        raise ImportError(
            "The GUI functionality requires tkinter which is not installed. "
            "Please install tkinter on your system to use this feature."
        )

# Define what symbols to export when using "from pyGCxGC import *"
__all__ = [
    # Modules
    "parsing", 
    "main",
    "processing",
    # Parsing functions
    "parse_2D_chromatogram",
    "parse_csv",
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
    # GUI functions
    "launch_mask_creator"
]