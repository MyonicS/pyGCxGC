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

from .processing import mask_chromatogram

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
    "mask_chromatogram"
]