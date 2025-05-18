# Using the Mask Creator GUI

The Mask Creator GUI is a graphical tool for creating masks for 2D chromatograms. These masks can be used to isolate specific regions of interest in a chromatogram for further analysis.

## Starting the GUI

There are several ways to start the Mask Creator GUI:

1. **From Python**:
   ```python
   import pyGCxGC as gcgc
   gcgc.launch_mask_creator()
   ```

2. **From the command line**:
   After installing pyGCxGC, you can use the provided script:
   ```bash
   pygcxgc-maskcreator
   ```

## Using the GUI

### Loading a Chromatogram

1. Click the "Load Chromatogram File" button
2. Select a CSV file containing chromatogram data
3. Enter the modulation time in seconds when prompted

### Drawing a Mask

The GUI provides two drawing tools:

- **Rectangle**: Click and drag to create a rectangular selection
- **Lasso**: Click and drag to create a free-form selection

To add the selection to the mask, click "Add Selection to Mask".

> **Note on Coordinate Systems**: The GUI properly handles coordinate transformations between the display coordinates and the underlying array coordinates. The y-axis direction differs between these coordinate systems (display coordinates have y increasing upward, while array coordinates have y increasing downward). This ensures that masks are created with the correct orientation.

### User Interface Features

#### Tooltips

Hover over buttons and controls to see helpful tooltips explaining their functionality.

#### Status Bar

The status bar at the bottom of the window provides information about:
- Current operation status
- Selected region dimensions
- Mask coverage statistics
- File operations

#### Colorbar

A colorbar shows the signal intensity scale for the chromatogram display. The scale uses the square root of intensity values to better visualize the dynamic range.

### Common Tasks

#### Creating a New Mask

1. Load a chromatogram
2. Enter a name for your mask in the "Mask Name" field
3. Select a drawing tool (Rectangle or Lasso)
4. Draw on the chromatogram by clicking and dragging
5. Click "Add Selection to Mask" to add the selection to your mask
6. Repeat steps 3-5 to build up a complex mask
7. Save the mask when finished

#### Loading and Editing an Existing Mask

1. Load a chromatogram with the same dimensions as the one used to create the mask
2. Click "Load Mask" and select a .tif mask file
3. Make additional selections and add them to the mask
4. Save the updated mask

### Saving a Mask

1. Enter a name for the mask in the "Mask Name" field
2. Click the "Save Mask" button
3. Select a location to save the mask as a .tif file

## Using the Created Mask

Once you have created a mask, you can use it with pyGCxGC's masking functions:

```python
import pyGCxGC as gcgc
import matplotlib.pyplot as plt
import numpy as np

# Load a chromatogram
chrom = gcgc.parse_2D_chromatogram(
    'example_data/example_chromatograms/Example_FID.csv',
    modulation_time=20
)

# Apply the mask
masked_chrom = gcgc.mask_chromatogram(chrom.chrom_2D, 'path/to/your/mask.tif')

# Visualize the masked chromatogram
plt.figure(figsize=(10,10/1.615))
plt.imshow(np.sqrt(masked_chrom), cmap='viridis', 
           extent=tuple(chrom.limits), interpolation='bilinear', aspect='auto')
plt.xlabel('Retention time 1 (min)')
plt.ylabel('Retention time 2 (s)')
plt.colorbar(label=r'$\sqrt{\mathrm{intensity}}$')
plt.title('Masked Chromatogram')
plt.show()

# Integrate the masked region
result = gcgc.integrate_2D(masked_chrom)
print(f"Integrated value: {result}")
```

## Integrating Multiple Masks

You can also create multiple masks for different regions and integrate them together:

```python
# Integrate using multiple masks
results = gcgc.integrate_masks(
    chrom.chrom_2D,
    masks='path/to/mask/folder/',
    mask_names='infer'
)

# Display results as a DataFrame
import pandas as pd
results_df = pd.DataFrame([results], index=['Sample 1'])
results_df
```
