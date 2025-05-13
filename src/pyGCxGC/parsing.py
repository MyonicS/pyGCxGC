import pandas as pd
import pyGCxGC.main as gcgc
import os
from typing import Union, Callable, Optional



def parse_csv(path)->pd.DataFrame: #from a CSV file
    df = pd.read_csv(path, sep = ',',  skiprows = 1)
    df.columns = ['Time(ms)', 'Time(min)', 'unknown','Absolute Intensity']
    df['Ret.Time'] = df['Time(min)']
    df['Ret.Time']-=df['Ret.Time'][0]
    df['Ret.Time[s]']=df['Ret.Time']*60
    df.drop(['Time(ms)','Time(min)','unknown'], axis=1, inplace=True)
    return df



def parse_2D_chromatogram(
    data: Union[pd.DataFrame, str, os.PathLike], 
    modulation_time: float, 
    sampling_interval: Union[float, str] = 'infer',
    shift: float = 0, 
    baseline_type: Union[str, Callable] = 'stridewise',
    normalize: str = 'volume', 
    solvent_cutoff: float = 0
) -> gcgc.GCxGC_FID:
    """
    Parses a 2D chromatogram from input data (frame or file), applies preprocessing steps, and returns a processed 2D chromatogram.

    Parameters
    ----------
    data : pandas.DataFrame or str or os.PathLike
        The input chromatogram data. Can be a pandas DataFrame or a file path to a CSV file.
    modulation_time : float
        The modulation time in seconds.
    sampling_interval : float or str, optional, default='infer'
        The sampling interval in milliseconds. If 'infer', it is calculated from the data.
    shift : float, optional, default=0
        Phase shift to apply to the 2D chromatogram.
    baseline_type : str or callable, optional, default='stridewise'
        The type of baseline correction to apply. Options are:
        - 'stridewise': Applies stridewise subtraction of the minimum.
        - 'global': Subtracts the minimum absolute intensity as a global baseline.
        - callable: A custom function for baseline correction.
    normalize : str, optional, default='volume'
        The normalization method to apply. Options are:
        - 'volume': Normalizes by volume.
        - 'max': Normalizes by the maximum intensity.
        - None: No normalization is applied.
    solvent_cutoff : float, optional, default=0
        The solvent cutoff value. Signals below this value are set to zero.

    Returns
    -------
    GCxGC_FID
        A processed 2D chromatogram object containing the original and 2D chromatogram data.

    Raises
    ------
    TypeError
        If `data` is not a pandas DataFrame or a valid file path.
    ValueError
        If `sampling_interval` is not a float or 'infer'.
    AssertionError
        If the sampling interval is not an integer multiple of the modulation time.

    Notes
    -----
    - The function supports baseline correction and normalization.
    - Padding to account for non-integer multiples of modulation time and sampling interval is not implemented.
    """
    if isinstance(data, pd.DataFrame):
        chrom = data
        name = 'Chromatogram'
    elif isinstance(data, (str, os.PathLike)):
        chrom = parse_csv(data)
        name = os.path.basename(data)
    else:
        raise TypeError(f"data must be a pandas DataFrame or a file path, not {type(data)}")
    
    # setting the Signal to 0 below the solvent cutoff
    if solvent_cutoff > 0:
        chrom = gcgc.split_solvent(chrom, solvent_cutoff)
    else:
        pass

    #checking whether the sampling interval is an integer multiple of the modulation time
    if sampling_interval =='infer':
        sampling_interval = 1000 * float(chrom['Ret.Time[s]'].diff()[1]) # type: ignore
    elif isinstance(sampling_interval, str):
        raise ValueError(f"sampling_interval must be a float or 'infer', not {type(sampling_interval)}")

    # asserting that the modulation time is an integer multiple of the sampling interval
    assert gcgc.is_integer_multiple(modulation_time, sampling_interval),\
        f"Sampling interval {sampling_interval} is not an integer multiple of the modulation time {modulation_time}.\
        \n Padding to account for this is not implemented"


    # 2D generation
    chrom = gcgc.add_split(chrom,modulation_time,sampling_interval)
    chrom_2D = gcgc.convert_to2D(chrom,modulation_time)
    if shift != 0 :
        chrom_2D = gcgc.shift_phase(chrom_2D, shift)

    #Baseline correction
    if baseline_type == 'stridewise':
        chrom_2D = gcgc.baseline_stridewise(chrom_2D)
    elif baseline_type == 'global':
        chrom_2D = chrom_2D - chrom['Absolute Intensity'].min()

    elif callable(baseline_type): # type: ignore
        chrom_2D = baseline_type(chrom_2D)
    else:
        pass

    #Normalization
    if normalize == 'volume':
        chrom_2D = gcgc.normalize_by_volume(chrom_2D)
    elif normalize == 'max':
        chrom_2D = chrom_2D / chrom_2D.max()
    else:
        print('No normalization applied')


    # make a 2D chromatogram class
    Output = gcgc.GCxGC_FID(chrom, chrom_2D, modulation_time, sampling_interval, shift, solvent_cutoff) # type: ignore
    Output.name = name # type: ignore

    
    return Output