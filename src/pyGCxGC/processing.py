from typing import Union
import os
import pandas as pd
import numpy as np
import tifffile

def mask_chromatogram(chromatogram: Union[pd.DataFrame, np.ndarray], mask_path: Union[str, os.PathLike]):
    """
    Masks the chromatogram by multiplication with a binary mask.
    Parameters
    ----------
    chromatogram : Chromatogram
        The chromatogram to be masked.
    mask_path : str
        The path to the mask file. The mask needs to be a binary mask saved as .tif with the exact same dimensions as the chromatogram.

    Returns
    -------
    masked_chromatogram : Chromatogram
        The masked chromatogram.
    """
    mask = tifffile.imread(mask_path)
    if mask.shape != chromatogram.shape:
        raise ValueError(f"Mask shape {mask.shape} does not match chromatogram shape {chromatogram.shape}.")
    if np.max(mask) == 255:
        mask = mask / 255
    if np.max(mask) == 1:
        mask = mask
    masked_chromatogram = chromatogram * mask
    return masked_chromatogram
