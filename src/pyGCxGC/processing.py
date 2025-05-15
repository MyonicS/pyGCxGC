from typing import Union
import os
import pandas as pd
import numpy as np
import tifffile
from scipy.integrate import trapezoid

def mask_chromatogram(chromatogram: Union[pd.DataFrame, np.ndarray], mask_path: Union[str, os.PathLike])-> Union[pd.DataFrame, np.ndarray]:
    """
    Masks the chromatogram by multiplication with a binary mask.

    Parameters
    ----------
    chromatogram : Chromatogram
        The chromatogram to be masked.
    mask_path : str
        The path to the mask file. The mask needs to be a binary mask saved as .tif with the exact same dimensions as the chromatogram.
        To generate a mask, you can save the 2D chromatogram as a .tif file and then use an image processing software such as ImageJ to create a binary mask.

    Returns
    -------
    masked_chromatogram : Chromatogram
        The masked chromatogram.
    """
    mask = tifffile.imread(mask_path)
    if mask.shape != chromatogram.shape:
        raise ValueError(f"Mask shape {mask.shape} does not match chromatogram shape {chromatogram.shape}.")
    if np.max(mask) == 255:
        unique, counts = np.unique(mask, return_counts=True)
        mask = mask / 255
    if np.max(mask) == 1:
        mask = mask
    masked_chromatogram = chromatogram * mask
    return masked_chromatogram



def integrate_2D(Chrom_2D: Union[pd.DataFrame, np.ndarray]) -> float:
    """
    Integrates a 2D chromatogram by integrating over the rows and then over the columns.
    Arguments:
    Chrom_2D : pandas.DataFrame
        The input 2D DataFrame representing the chromatogram. Index is the Retention time 2 (y), Columns are the Retention time 1 (x).
    Returns:
    --------
    float
        The integrated volume of the 2D chromatogram.
    """
    array_for_integral = np.array(Chrom_2D)
    #integrate over rows
    row_integrated_chrom = trapezoid(array_for_integral, axis=0)
    #integrate over columns
    volume = trapezoid(row_integrated_chrom, axis=0)
    return volume



def integrate_masks(chromatogram_2D: Union[pd.DataFrame, np.ndarray], masks: Union[list, os.PathLike, str], mask_names: Union[list, str] = 'infer') -> dict:
    """
    Applies a list of masks to a 2D chromatogram and returns the integrated areas of the masked regions.
    
    Parameters
    ----------
    chromatogram_2D : Union[pd.DataFrame, np.ndarray]
        2D chromatogram to be integrated.
    masks : Union[list, os.PathLike, str]
        List of mask paths or a path to a directory containing the masks.
        The masks must be .tif files.
    mask_names : Union[list, str]
        List of names for the masks, or 'infer' to infer from filenames.
        
    Returns
    -------
    Integrals : dict
        A dictionary where the keys are the names of the masks and the values are the integrated areas of the masked regions.
    """
    # Determine mask paths
    if isinstance(masks, (str, os.PathLike)):
        mask_dir = str(masks)
        mask_list = [os.path.join(mask_dir, f) for f in os.listdir(mask_dir) if f.lower().endswith('.tif')]
        if len(mask_list) == 0:
            raise ValueError('No .tif masks found in the provided directory')
    elif isinstance(masks, list):
        mask_list = masks
        if len(mask_list) == 0:
            raise ValueError('The mask list is empty')
    else:
        raise TypeError('masks must be a list of paths or a path to a directory containing the masks')

    # Infer mask names if needed
    if mask_names == 'infer':
        mask_names = [os.path.splitext(os.path.basename(mask))[0] for mask in mask_list]
    elif isinstance(mask_names, list):
        if len(mask_names) != len(mask_list):
            raise ValueError('Length of mask_names does not match number of masks')
    else:
        raise TypeError('mask_names must be a list or "infer"')

    Integrals = {}
    for i, mask_path in enumerate(mask_list):
        masked = mask_chromatogram(chromatogram_2D, mask_path)
        if not isinstance(masked, pd.DataFrame):
            masked = pd.DataFrame(masked)
        Integral = integrate_2D(masked)
        Integrals[mask_names[i]] = Integral

    return Integrals
