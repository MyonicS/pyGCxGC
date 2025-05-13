
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import glob
import scipy.integrate as integrate
from typing import Union, Callable, Optional, List, Dict, Any, Tuple


#define a GCxGC FID class with attributes: upon initilization only the name is requiered
"""
- name :Sample name
- Date
- sampling rate
- modulation time
- shift
- solvent cutoff
- 1D
- 2D
"""

class GCxGC_FID:

    def __init__(self, chrom_1D: pd.DataFrame, chrom_2D: pd.DataFrame, sampling_interval, modulation_time, shift=None, solvent_cutoff=None):
        self.chrom_1D = chrom_1D
        self.chrom_2D = chrom_2D
        self.sampling_interval = sampling_interval
        self.modulation_time = modulation_time
        self.shift = shift
        self.solvent_cutoff = solvent_cutoff
        self.name = None
        self.date = None
        self.limits = [
            self.chrom_1D['Ret.Time[s]'].min()/60,
            self.chrom_1D['Ret.Time[s]'].max()/60,
            self.chrom_2D.index.min(),
            self.chrom_2D.index.max()
        ]




def split_solvent(df: pd.DataFrame, solvent_time: Union[int, float] = 0) -> pd.DataFrame:
    """
    Sets the intensity to 0 for rows in the DataFrame where the retention time is less than or equal to the specified solvent time.

    Parameters:
    df (pd.DataFrame): The input DataFrame containing chromatographic data. 
                       It must have columns 'Ret.Time[s]' and 'Absolute Intensity'.
    solvent_time (Union[int, float]): The retention time threshold below which the intensity will be set to 0.

    Returns:
    pd.DataFrame: The modified DataFrame with updated intensity values.
    """
    df.loc[df['Ret.Time[s]'] <= solvent_time, 'Absolute Intensity'] = 0
    return df

def add_split(df,modulation_time,sampling_interval):#split time in s, sampling interval in ms
    """
    Splits a DataFrame into segments based on the specified split time and sampling interval.
    
    Parameters:
    -----------
    df : pandas.DataFrame
        The input DataFrame containing chromatographic data. It is expected to have 
        an index that represents the time points of the data.
    modulation_time : float
        The time duration (in s) for each split segment.
    sampling_interval : float
        The time interval (in s) between consecutive data points in the DataFrame.
    Returns:
    --------
    pandas.DataFrame
        The modified DataFrame with an additional column 'split_no_fromindex' that 
        indicates the split segment number for each row.
        """
    rows_splitting = modulation_time/sampling_interval*1000
    df['split_no_fromindex'] = df.index//rows_splitting 
    while len(df[df['split_no_fromindex']==df['split_no_fromindex'].max()]) < len(df[df['split_no_fromindex']==df['split_no_fromindex'].min()]):
        df.loc[len(df)+1] = df.iloc[-1]
    return df



def min_correct(df): # 'global minimum' as baseline
    df['Absolute Intensity'] = df['Absolute Intensity'] - df['Absolute Intensity'].min()
    return df

def baseline_stridewise(df_array):
    """
    Subtracts the minimum in each stride as baseline
    """
    df_array = df_array - df_array.min(axis=0)
    return df_array

def convert_to2D(df:pd.DataFrame, modulation_time:float)->pd.DataFrame:
    """
    Generates a 2D Chromatogram from a 1D chromatogram DataFrame.
    Arguments:
    df : pandas.DataFrame
        The input DataFrame containing chromatographic data. It is expected to have 
        columns 'split_no_fromindex' and 'Absolute Intensity'.
    modulation_time : float
        The time duration (in s) for each split segment.
    Returns:
    --------
    pandas.DataFrame
        A 2D DataFrame where the index represents the retention time and the columns 
        represent the split time. The values in the DataFrame are the absolute intensities.
    """

    df_short = df[['split_no_fromindex','Absolute Intensity']]
    array_list = []
    
    for i in range(0,int(df_short['split_no_fromindex'].max())):
        array_list.append(df_short[df_short['split_no_fromindex']==i]['Absolute Intensity'].values)

    #turn arraylist into an 2D array
    array = np.zeros((len(array_list),len(array_list[0])))
    for i in range(0,len(array_list)):
        array[i,:] = array_list[i]

    index_list_retention_time = []
    for i in range(len(array_list)):
        index_list_retention_time.append(i*modulation_time)

    columns_splittime = []
    for i in range(len(array_list[0])):
        columns_splittime.append(round(i*modulation_time/len(array_list[0]),3))

    df_array = pd.DataFrame(array, index = index_list_retention_time, columns = columns_splittime)
    df_array= df_array.T
    df_array = df_array.iloc[::-1]
    return df_array




def shift_phase(df_array, shift):
    """
    Adjust the phase of a 2D chromatogram by shifting the rows.
    Arguments:
    df_array : pandas.DataFrame
        The input 2D DataFrame representing the chromatogram. Index is the Retention time 2 (y), Columns are the Retention time 1 (x).
    shift : int
        The number of rows to shift the DataFrame. Positive values shift down, negative values shift up.
    Returns:
    --------
    pandas.DataFrame
        The shifted 2D DataFrame.
    """
    df_array_shifted = np.roll(df_array, shift, axis=0)
    return df_array_shifted

import tifffile


def normalize_by_volume(df_array):
    """
    Normalizes the Voume of a 2D chromatogram to 1.
    Arguments:
    df_array : pandas.DataFrame
        The input 2D DataFrame representing the chromatogram. Index is the Retention time 2 (y), Columns are the Retention time 1 (x).
    Returns:
    --------
    pandas.DataFrame
        The normalized 2D DataFrame.
    """
    arrray_for_integral = np.array(df_array)
    #integrate over rows
    row_integrated_pre = integrate.trapezoid(arrray_for_integral, axis=0)
    #integrating the new array
    integral_non_norm = integrate.trapezoid(row_integrated_pre, axis=0)
    df_array_norm = df_array/integral_non_norm
    return df_array_norm
    
def integrate_masked(df_norm_array, maskpath):
    mask =  tifffile.imread(maskpath)/255 # if the mask is binary no need to divide by 255
    df_norm_array_masked  = df_norm_array*mask

    array_norm_mask_diarom = np.array(df_norm_array_masked)
    #integrate over rows
    row_integrated = integrate.trapezoid(array_norm_mask_diarom, axis=0)
    #integrating the new array
    column_integrated = integrate.trapezoid(row_integrated, axis=0)
    return column_integrated
    


def mask_integrate(df_array_norm, mask_dir):
    mask_list = glob.glob(mask_dir + '*.tif')
    #get the mask names
    mask_names = []
    for i in range(len(mask_list)):
        mask_name = mask_list[i].split('\\')[-1].split('.')[0]
        #split off the 'Mask_' part
        mask_names.append(mask_name.split('Mask_')[-1])


    integral_list = []
    for i in range(len(mask_list)):
        integral_list.append(integrate_masked(df_array_norm, mask_list[i]))

    # make a dataframe with the integral values and the mask_names as column names
    df_integral = pd.DataFrame([integral_list], columns = mask_names)
    df_integral['unassigned'] = 1-df_integral[mask_names].sum(axis=1)
    return df_integral

# def process_chromatogram(filepath, modulation_time, sampling_interval, mask_dir,shift=0, solvent_time=0):
#     df = parse_chromatogram(filepath)
#     df = split_solvent(df, solvent_time)
#     df = add_split(df,modulation_time,sampling_interval)
#     df_array = convert_to2D(df,modulation_time)
#     df_array  = baseline_stridewise(df_array)
#     df_array = shift_phase(df_array, shift)
#     df_array_norm = normalize_array(df_array)
#     df_integral = mask_integrate(df_array_norm, mask_dir)
#     return df_integral, df_array_norm






# from scipy import sparse
# from scipy.sparse.linalg import spsolve
# # def baseline_als(y, lam, p, niter=50):
# #     L = len(y)
# #     D = sparse.diags([1,-2,1],[0,-1,-2], shape=(L,L-2))
# #     D = lam * D.dot(D.transpose()) # Precompute this term since it does not depend on `w`
# #     w = np.ones(L)
# #     W = sparse.spdiags(w, 0, L, L)
# #     for i in range(niter):
# #         W.setdiag(w) # Do not create a new matrix, just update diagonal values
# #         Z = W + D
# #         z = spsolve(Z, w*y)
# #         w = p * (y > z) + (1-p) * (y < z)
# #     return z


def is_integer_multiple(larger, smaller, tolerance=1e-10):
    """Check if larger is an integer multiple of smaller within a tolerance."""
    ratio = larger / smaller
    return abs(round(ratio) - ratio) < tolerance