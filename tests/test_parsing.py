import os
import pandas as pd
import numpy as np
# import pytest
from pyGCxGC.parsing import parse_csv, parse_2D_chromatogram
from pyGCxGC.main import GCxGC_FID

# Get the base directory of the project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_DATA_DIR = os.path.join(BASE_DIR, "example_data")
EXAMPLE_CHROMATOGRAM = os.path.join(EXAMPLE_DATA_DIR, "example_chromatograms", "Example_FID.csv")



def test_parse_2D_chromatogram_from_file():
    """Test parse_2D_chromatogram with a file path."""
    # Common parameters for GCxGC processing
    modulation_time = 20.0  # seconds, typical value
    
    # Test parsing from file
    chromatogram = parse_2D_chromatogram(
        data=EXAMPLE_CHROMATOGRAM,
        modulation_time=modulation_time
    )
    
    # Verify the result is a GCxGC_FID object
    assert isinstance(chromatogram, GCxGC_FID)
    
    # Verify the chromatogram has the expected attributes
    assert hasattr(chromatogram, "chrom_1D")
    assert hasattr(chromatogram, "chrom_2D")
    
    # Verify the name is set correctly (should be the filename)
    assert chromatogram.name == "Example_FID.csv"
    assert chromatogram.modulation_time == modulation_time
    assert chromatogram.sampling_interval == 4.0


def test_parse_2D_chromatogram_from_dataframe():
    """Test parse_2D_chromatogram with a pandas DataFrame."""
    # First load the data as a DataFrame
    df = parse_csv(EXAMPLE_CHROMATOGRAM)
    
    # Common parameters for GCxGC processing
    modulation_time = 20.0  # seconds

    # Test parsing from DataFrame
    chromatogram = parse_2D_chromatogram(
        data=df,
        modulation_time=modulation_time,
        name="Test Chromatogram"  # Set a custom name
    )
    
    # Verify the result is a GCxGC_FID object
    assert isinstance(chromatogram, GCxGC_FID)
    
    # Verify the name for DataFrames defaults to 'Chromatogram'
    assert chromatogram.name == "Test Chromatogram"


def test_parse_2D_chromatogram_with_options():
    """Test parse_2D_chromatogram with various options."""
    # Common parameters for GCxGC processing
    modulation_time = 20  # seconds
    shift = 2  # rows to shift
    solvent_cutoff = 1.0  # min
    
    # Test parsing with additional options - Use volume normalization since max may return a numpy array
    chromatogram = parse_2D_chromatogram(
        data=EXAMPLE_CHROMATOGRAM,
        modulation_time=modulation_time,
        shift=shift,
        solvent_cutoff=solvent_cutoff,
        normalize="volume"  # Use volume normalization as it's the default
    )
    
    # Verify the result has the specified parameters
    assert chromatogram.shift == shift
    assert chromatogram.solvent_cutoff == solvent_cutoff
    

