"""
This module provides a GUI for creating masks for 2D chromatograms.
"""

import os
import numpy as np
import pandas as pd
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.backends._backend_tk import NavigationToolbar2Tk
from matplotlib.figure import Figure
import matplotlib.patches as patches
from matplotlib.widgets import RectangleSelector, LassoSelector
from matplotlib.path import Path
import tifffile
import traceback

from typing import Union, Optional, List, Tuple, Any
import pyGCxGC as gcgc


class ToolTip:
    """
    Create a tooltip for a given widget
    """
    def __init__(self, widget, text=''):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.id = None
        self.x = self.y = 0
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
    
    def enter(self, event=None):
        """Display the tooltip when mouse enters the widget"""
        self.schedule()
    
    def leave(self, event=None):
        """Hide the tooltip when mouse leaves the widget"""
        self.unschedule()
        self.hidetip()
    
    def schedule(self):
        """Schedule showing the tooltip"""
        self.unschedule()
        self.id = self.widget.after(500, self.showtip)
    
    def unschedule(self):
        """Unschedule showing the tooltip"""
        id = self.id
        self.id = None
        if id:
            self.widget.after_cancel(id)
    
    def showtip(self):
        """Show the tooltip"""
        if self.tipwindow or not self.text:
            return
        x, y, cx, cy = self.widget.bbox("insert")
        x = x + self.widget.winfo_rootx() + 25
        y = y + cy + self.widget.winfo_rooty() + 25
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(tw, text=self.text, background="#ffffe0",
                           relief=tk.SOLID, borderwidth=1,
                           wraplength=250, justify=tk.LEFT)
        label.pack(padx=4, pady=4)
    
    def hidetip(self):
        """Hide the tooltip"""
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()


def create_tooltip(widget, text):
    """Create a tooltip for a widget"""
    return ToolTip(widget, text)


class MaskCreatorGUI:
    """
    A GUI for creating and editing masks for 2D chromatograms.
    
    This class provides a graphical interface for:
    - Loading and displaying 2D chromatograms
    - Drawing masks using different tools (rectangle, lasso)
    - Saving masks as .tif files for use with pyGCxGC
    """
    
    def __init__(self, root: tk.Tk):
        """
        Initialize the MaskCreatorGUI.
        
        Parameters
        ----------
        root : tk.Tk
            The root Tkinter window
        """
        # Set matplotlib font to Liberation Sans
        plt.rcParams['font.family'] = 'Liberation Sans'
        
        self.root = root
        self.root.title("pyGCxGC Mask Creator")
        self.root.geometry("1200x800")
        
        # Variables to store data
        self.chromatogram = None  # Will hold the GCxGC_FID object
        self.mask = None          # Will hold the current mask
        self.mask_name = tk.StringVar(value="New Mask")
        
        # Status message
        self.status_var = tk.StringVar(value="Ready")
        
        # Current drawing mode
        self.drawing_mode = tk.StringVar(value="rectangle")
        
        # Setup UI
        self._create_ui()
        
        # Initialize selector (will be set when chromatogram is loaded)
        self.selector = None
        self.selected_points = []
        self.current_mask_color = 'red'
        
        # Create a reference for the colorbar
        self.cbar = None
        
    def _create_ui(self):
        """Create the UI components"""
        # Main frame layout
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left panel (controls)
        left_frame = ttk.Frame(main_frame, width=300)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        
        # Right panel (visualization)
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # Create and add controls to left panel
        self._create_left_panel(left_frame)
        
        # Create matplotlib figure and canvas in right panel
        self._create_right_panel(right_frame)
        
        # Create status bar
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        status_label = ttk.Label(status_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_label.pack(fill=tk.X, padx=5, pady=2)
    
    def _create_left_panel(self, parent):
        """Create the left control panel"""
        # Chromatogram load section
        chrom_frame = ttk.LabelFrame(parent, text="Chromatogram", padding=10)
        chrom_frame.pack(fill=tk.X, pady=(0, 10))
        
        load_btn = ttk.Button(
            chrom_frame, 
            text="Load Chromatogram File", 
            command=self._load_chromatogram
        )
        load_btn.pack(fill=tk.X, pady=5)
        create_tooltip(load_btn, "Load a 2D chromatogram from a CSV file")
        
        # Display current chromatogram
        self.chrom_name_label = ttk.Label(chrom_frame, text="No chromatogram loaded")
        self.chrom_name_label.pack(fill=tk.X, pady=5)
        
        # Drawing tools section
        draw_frame = ttk.LabelFrame(parent, text="Drawing Tools", padding=10)
        draw_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Drawing mode options
        rect_btn = ttk.Radiobutton(
            draw_frame, 
            text="Rectangle",
            variable=self.drawing_mode,
            value="rectangle",
            command=self._update_selector
        )
        rect_btn.pack(anchor=tk.W, pady=2)
        create_tooltip(rect_btn, "Draw rectangular selections on the chromatogram")
        
        lasso_btn = ttk.Radiobutton(
            draw_frame, 
            text="Lasso",
            variable=self.drawing_mode,
            value="lasso",
            command=self._update_selector
        )
        lasso_btn.pack(anchor=tk.W, pady=2)
        create_tooltip(lasso_btn, "Draw freeform selections on the chromatogram")
        
        # Clear button
        clear_btn = ttk.Button(
            draw_frame, 
            text="Clear Current Selection", 
            command=self._clear_selection
        )
        clear_btn.pack(fill=tk.X, pady=5)
        create_tooltip(clear_btn, "Clear the current selection from the display")
        
        # Mask operations section
        mask_frame = ttk.LabelFrame(parent, text="Mask Operations", padding=10)
        mask_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Mask name entry
        ttk.Label(mask_frame, text="Mask Name:").pack(anchor=tk.W, pady=2)
        name_entry = ttk.Entry(
            mask_frame, 
            textvariable=self.mask_name
        )
        name_entry.pack(fill=tk.X, pady=5)
        create_tooltip(name_entry, "Enter a name for your mask (used when saving)")
        
        # Add current selection to mask
        add_btn = ttk.Button(
            mask_frame, 
            text="Add Selection to Mask", 
            command=self._add_to_mask
        )
        add_btn.pack(fill=tk.X, pady=5)
        create_tooltip(add_btn, "Add the current selection to the mask")
        
        # Create new empty mask
        new_btn = ttk.Button(
            mask_frame, 
            text="Create New Mask", 
            command=self._create_new_mask
        )
        new_btn.pack(fill=tk.X, pady=5)
        create_tooltip(new_btn, "Create a new empty mask, discarding the current one")
        
        # Save/load mask buttons
        save_btn = ttk.Button(
            mask_frame, 
            text="Save Mask", 
            command=self._save_mask
        )
        save_btn.pack(fill=tk.X, pady=5)
        create_tooltip(save_btn, "Save the current mask as a TIFF file")
        
        load_mask_btn = ttk.Button(
            mask_frame, 
            text="Load Mask", 
            command=self._load_mask
        )
        load_mask_btn.pack(fill=tk.X, pady=5)
        create_tooltip(load_mask_btn, "Load an existing mask from a TIFF file")
        
        # Help section
        help_frame = ttk.LabelFrame(parent, text="Help", padding=10)
        help_frame.pack(fill=tk.X, pady=(0, 10))
        
        help_text = (
            "1. Load a chromatogram\n"
            "2. Select drawing tool\n"
            "3. Draw on the chromatogram\n"
            "4. Add selection to mask\n"
            "5. Save mask as .tif\n\n"
            "Pan/Zoom: Use toolbar"
        )
        ttk.Label(help_frame, text=help_text, justify=tk.LEFT).pack(fill=tk.X)
    
    def _create_right_panel(self, parent):
        """Create the right visualization panel with matplotlib"""
        # Create a frame for matplotlib figure
        self.fig_frame = ttk.Frame(parent)
        self.fig_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create Figure and Axes with constrained layout to handle colorbar properly
        self.fig = Figure(figsize=(8, 6), dpi=100, constrained_layout=True)
        
        # Set Liberation Sans font for all text in the figure
        from matplotlib import rcParams
        rcParams['font.family'] = 'Liberation Sans'
        
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel('Retention time 1 (min)')
        self.ax.set_ylabel('Retention time 2 (s)')
        self.ax.set_title('2D Chromatogram')
        
        # Add the figure to the tkinter window
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.fig_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Add toolbar
        toolbar_frame = ttk.Frame(parent)
        toolbar_frame.pack(fill=tk.X)
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()
    
    def _load_chromatogram(self):
        """Load a chromatogram file"""
        # Open file dialog for selecting a chromatogram
        self.status_var.set("Selecting chromatogram file...")
        file_path = filedialog.askopenfilename(
            title="Select a chromatogram file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if not file_path:
            self.status_var.set("Ready")
            return
        
        try:
            # Ask for modulation time
            self.status_var.set("Waiting for modulation time input...")
            modulation_dialog = tk.Toplevel(self.root)
            modulation_dialog.title("Modulation Time")
            modulation_dialog.geometry("300x150")
            modulation_dialog.grab_set()
            
            ttk.Label(modulation_dialog, text="Enter modulation time in seconds:").pack(pady=10)
            
            modulation_time_var = tk.DoubleVar(value=20.0)
            modulation_entry = ttk.Entry(modulation_dialog, textvariable=modulation_time_var)
            modulation_entry.pack(pady=10, padx=20, fill=tk.X)
            
            # Button to confirm
            def on_ok():
                modulation_dialog.destroy()
            
            ttk.Button(modulation_dialog, text="OK", command=on_ok).pack(pady=10)
            
            # Wait for dialog to close
            self.root.wait_window(modulation_dialog)
            
            modulation_time = modulation_time_var.get()
            
            if modulation_time <= 0:
                messagebox.showerror("Error", "Modulation time must be greater than 0")
                self.status_var.set("Ready")
                return
            
            # Load the chromatogram
            self.status_var.set(f"Loading chromatogram from {os.path.basename(file_path)}...")
            self.chromatogram = gcgc.parse_2D_chromatogram(
                file_path, 
                modulation_time=modulation_time,
                sampling_interval='infer'
            )
            
            # Update UI
            self.chrom_name_label.config(text=f"Loaded: {self.chromatogram.name}")
            
            # Initialize an empty mask
            self._create_new_mask()
            
            # Display the chromatogram
            self._display_chromatogram()
            
            self.status_var.set(f"Chromatogram loaded: {self.chromatogram.name}")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load chromatogram: {str(e)}")
            self.status_var.set("Error loading chromatogram")
    
    def _display_chromatogram(self):
        """Display the loaded chromatogram"""
        if self.chromatogram is None:
            return
        
        self.status_var.set("Updating display...")
        
        # Remove any existing colorbar before clearing figure
        if hasattr(self, 'cbar') and self.cbar is not None:
            self.cbar.remove()
            self.cbar = None
        
        # Clear the figure completely
        self.fig.clear()
        
        # Recreate the axes
        self.ax = self.fig.add_subplot(111)
        
        # Plot the chromatogram
        im = self.ax.imshow(
            np.sqrt(self.chromatogram.chrom_2D), 
            cmap='viridis', 
            extent=tuple(self.chromatogram.limits),
            interpolation='bilinear', 
            aspect='auto'
        )
        
        # Create a single colorbar
        # This is the only place we create a colorbar in the entire application
        self.cbar = self.fig.colorbar(im, ax=self.ax)
        self.cbar.set_label('Signal Intensity (sqrt)')
        
        # Set labels
        self.ax.set_xlabel('Retention time 1 (min)')
        self.ax.set_ylabel('Retention time 2 (s)')
        self.ax.set_title(f'2D Chromatogram: {self.chromatogram.name}')
        
        # If mask exists, overlay it
        if self.mask is not None:
            # Create a color map for the mask: transparent for 0, red with alpha=0.3 for 1
            mask_colors = np.zeros((self.chromatogram.chrom_2D.shape[0], self.chromatogram.chrom_2D.shape[1], 4))
            mask_colors[self.mask == 1] = [1, 0, 0, 0.3]  # Red with alpha=0.3
            self.ax.imshow(mask_colors, extent=tuple(self.chromatogram.limits), interpolation='nearest', aspect='auto')
            
            # Count the number of masked pixels
            masked_pixels = np.sum(self.mask)
            total_pixels = self.mask.size
            percent_masked = (masked_pixels / total_pixels) * 100
            
            self.status_var.set(f"Display updated. Mask covers {masked_pixels} pixels ({percent_masked:.2f}% of total)")
        else:
            self.status_var.set("Display updated")
        
        # Update canvas
        self.canvas.draw()
        
        # Set up the selector
        self._update_selector()
    
    def _update_selector(self):
        """Update the selector based on the current drawing mode"""
        if self.chromatogram is None:
            return
        
        # Remove existing selector if any
        if self.selector is not None:
            try:
                self.selector.disconnect_events()
            except:
                pass
            self.selector = None
        
        # Create new selector based on current mode
        if self.drawing_mode.get() == "rectangle":
            self.selector = RectangleSelector(
                self.ax, 
                self._on_rectangle_select,
                useblit=True,
                button=None,  # Use default button behavior
                minspanx=5, 
                minspany=5,
                spancoords='pixels',
                interactive=True
            )
        elif self.drawing_mode.get() == "lasso":
            self.selector = LassoSelector(
                self.ax, 
                self._on_lasso_select,
                button=None  # Use default button behavior
            )
    
    def _on_rectangle_select(self, eclick, erelease):
        """Callback for rectangle selection"""
        # Get the selection in data coordinates
        x1, y1 = eclick.xdata, eclick.ydata
        x2, y2 = erelease.xdata, erelease.ydata
        
        # Store the selection for later use
        self.selected_points = [(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))]
        
        # Update status bar
        area_rt1 = abs(x2 - x1)  # RT1 in minutes
        area_rt2 = abs(y2 - y1)  # RT2 in seconds
        self.status_var.set(f"Rectangle selected: RT1 range = {min(x1, x2):.2f}-{max(x1, x2):.2f} min, RT2 range = {min(y1, y2):.2f}-{max(y1, y2):.2f} s")
    
    def _on_lasso_select(self, vertices):
        """Callback for lasso selection"""
        # Store the lasso path
        self.selected_points = vertices
        
        # Update status bar
        num_points = len(vertices)
        self.status_var.set(f"Lasso selection with {num_points} vertices completed")
    
    def _clear_selection(self):
        """Clear the current selection"""
        self.selected_points = []
        # Redraw the chromatogram and mask without the selection
        self._display_chromatogram()
        self.status_var.set("Selection cleared")
    
    def _create_new_mask(self):
        """Create a new empty mask with the same dimensions as the chromatogram"""
        if self.chromatogram is None:
            messagebox.showwarning("Warning", "Please load a chromatogram first")
            return
        
        # Create an empty mask with the same dimensions as the chromatogram
        self.mask = np.zeros_like(self.chromatogram.chrom_2D, dtype=np.uint8)
        
        # Update display
        self._display_chromatogram()
        messagebox.showinfo("Info", "New empty mask created")
    
    def _add_to_mask(self):
        """Add the current selection to the mask"""
        if self.chromatogram is None or self.mask is None:
            messagebox.showwarning("Warning", "Please load a chromatogram and create a mask first")
            return
        
        if not self.selected_points:
            messagebox.showwarning("Warning", "No selection to add")
            return
        
        try:
            self.status_var.set("Adding selection to mask...")
            # Convert the mask to a temporary array for manipulation
            temp_mask = self.mask.copy()
            
            # Convert data coordinates to pixel/array coordinates
            # Note: In numpy arrays, the first index is row (y), the second is column (x)
            # But in the display coordinates, x is the first dimension and y is the second
            # For chromatograms, retention time 1 (minutes) is x-axis, retention time 2 (seconds) is y-axis
            data_to_array_x = lambda x: int((x - self.chromatogram.limits[0]) / 
                                            (self.chromatogram.limits[1] - self.chromatogram.limits[0]) * 
                                            self.chromatogram.chrom_2D.shape[1])
            
            # The y-axis starts at the top in display coordinates but at the bottom in array coordinates
            # This is the source of the flipping issue - we need to invert the y-coordinate
            data_to_array_y = lambda y: int((self.chromatogram.limits[3] - y) / 
                                            (self.chromatogram.limits[3] - self.chromatogram.limits[2]) * 
                                            self.chromatogram.chrom_2D.shape[0])
            
            # Apply the selection to the mask based on the drawing mode
            if self.drawing_mode.get() == "rectangle" and self.selected_points:
                # Get rectangle coordinates
                x1, y1, x2, y2 = self.selected_points[0]
                
                # Convert to array indices
                x1_idx = max(0, min(data_to_array_x(x1), self.mask.shape[1]-1))
                y1_idx = max(0, min(data_to_array_y(y1), self.mask.shape[0]-1))
                x2_idx = max(0, min(data_to_array_x(x2), self.mask.shape[1]-1))
                y2_idx = max(0, min(data_to_array_y(y2), self.mask.shape[0]-1))
                
                # Make sure x1_idx <= x2_idx and y1_idx <= y2_idx
                x1_idx, x2_idx = min(x1_idx, x2_idx), max(x1_idx, x2_idx)
                y1_idx, y2_idx = min(y1_idx, y2_idx), max(y1_idx, y2_idx)
                
                # Set the rectangle area to 1 in the mask
                temp_mask[y1_idx:y2_idx+1, x1_idx:x2_idx+1] = 1
                self.status_var.set(f"Added rectangle selection to mask ({x2_idx-x1_idx+1}x{y2_idx-y1_idx+1} pixels)")
                
            elif self.drawing_mode.get() == "lasso" and self.selected_points:
                try:
                    # Create a Path from the lasso vertices
                    path = Path(self.selected_points)
                    
                    # Create a grid of points
                    y, x = np.mgrid[:self.mask.shape[0], :self.mask.shape[1]]
                    points = np.vstack((x.flatten(), y.flatten())).T
                    
                    # Get array to data transform for points
                    array_to_data_x = lambda x: self.chromatogram.limits[0] + (x / self.mask.shape[1]) * (self.chromatogram.limits[1] - self.chromatogram.limits[0])
                    
                    # Invert the y coordinate to match the display orientation
                    array_to_data_y = lambda y: self.chromatogram.limits[3] - (y / self.mask.shape[0]) * (self.chromatogram.limits[3] - self.chromatogram.limits[2])
                    
                    # Transform points to data coordinates
                    points_data = np.vstack((array_to_data_x(points[:, 0]), array_to_data_y(points[:, 1]))).T
                    
                    # Check which points are inside the path
                    mask_points = path.contains_points(points_data)
                    mask_points = mask_points.reshape(self.mask.shape)
                    
                    # Set the lasso area to 1 in the mask
                    temp_mask[mask_points] = 1
                    
                    # Count the number of points added
                    num_added = np.sum(mask_points)
                    self.status_var.set(f"Added lasso selection to mask ({num_added} pixels)")
                    
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to process lasso selection: {str(e)}")
                    traceback.print_exc()
                    self.status_var.set("Error processing lasso selection")
                    return
            
            # Update the mask
            self.mask = temp_mask
            
            # Redraw
            self._display_chromatogram()
            
            # Clear the selection
            self.selected_points = []
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to add selection to mask: {str(e)}")
            traceback.print_exc()
            self.status_var.set("Error adding selection to mask")
    
    def _save_mask(self):
        """Save the current mask as a TIFF file"""
        if self.chromatogram is None or self.mask is None:
            messagebox.showwarning("Warning", "Please load a chromatogram and create a mask first")
            return
        
        # Open file dialog for saving the mask
        self.status_var.set("Selecting destination for mask file...")
        file_path = filedialog.asksaveasfilename(
            title="Save Mask",
            defaultextension=".tif",
            initialfile=f"{self.mask_name.get()}.tif",
            filetypes=[("TIFF files", "*.tif"), ("All files", "*.*")]
        )
        
        if not file_path:
            self.status_var.set("Ready")
            return
        
        try:
            self.status_var.set(f"Saving mask to {os.path.basename(file_path)}...")
            # Save the mask as a TIFF file (multiply by 255 to get binary mask)
            tifffile.imwrite(file_path, self.mask * 255)
            messagebox.showinfo("Success", f"Mask saved to {file_path}")
            self.status_var.set(f"Mask saved to {os.path.basename(file_path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save mask: {str(e)}")
            self.status_var.set("Error saving mask")
    
    def _load_mask(self):
        """Load a mask from a TIFF file"""
        if self.chromatogram is None:
            messagebox.showwarning("Warning", "Please load a chromatogram first")
            return
        
        # Open file dialog for selecting a mask
        self.status_var.set("Selecting mask file to load...")
        file_path = filedialog.askopenfilename(
            title="Select a mask file",
            filetypes=[("TIFF files", "*.tif"), ("All files", "*.*")]
        )
        
        if not file_path:
            self.status_var.set("Ready")
            return
        
        try:
            self.status_var.set(f"Loading mask from {os.path.basename(file_path)}...")
            # Load the mask
            loaded_mask = tifffile.imread(file_path)
            
            # Check if mask dimensions match the chromatogram
            if loaded_mask.shape != self.chromatogram.chrom_2D.shape:
                messagebox.showerror(
                    "Error", 
                    f"Mask dimensions {loaded_mask.shape} do not match chromatogram dimensions {self.chromatogram.chrom_2D.shape}"
                )
                self.status_var.set("Error: Mask dimensions do not match chromatogram")
                return
            
            # Normalize to binary (0 and 1)
            if np.max(loaded_mask) == 255:
                loaded_mask = loaded_mask / 255
            
            self.mask = loaded_mask.astype(np.uint8)
            
            # Set mask name from filename
            mask_name = os.path.splitext(os.path.basename(file_path))[0]
            self.mask_name.set(mask_name)
            
            # Update display
            self._display_chromatogram()
            messagebox.showinfo("Success", f"Mask loaded from {file_path}")
            self.status_var.set(f"Mask '{mask_name}' loaded successfully")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load mask: {str(e)}")
            self.status_var.set("Error loading mask")
    
    def _use_workspace_chromatogram(self):
        """Use a chromatogram from the current workspace variables"""
        try:
            # Get the list of variables in the user's namespace that are GCxGC_FID objects
            import inspect
            import sys
            
            # Look for GCxGC_FID objects in the calling frame's namespace
            frame = inspect.currentframe()
            try:
                # Go up frames until we find a frame with locals containing GCxGC_FID objects
                while frame:
                    frame_locals = frame.f_locals
                    # Find GCxGC_FID objects
                    chromatograms = {name: obj for name, obj in frame_locals.items() 
                                    if isinstance(obj, gcgc.GCxGC_FID)}
                    if chromatograms:
                        break
                    frame = frame.f_back
                    
                if not chromatograms:
                    # Look in globals as a last resort
                    chromatograms = {name: obj for name, obj in frame.f_globals.items() 
                                    if isinstance(obj, gcgc.GCxGC_FID)}
            finally:
                del frame  # Avoid reference cycles
            
            if not chromatograms:
                messagebox.showinfo("Info", "No GCxGC_FID objects found in the current workspace")
                return
            
            # Let the user choose which chromatogram to use
            if len(chromatograms) == 1:
                # Only one chromatogram available, use it directly
                chrom_name = list(chromatograms.keys())[0]
                self.chromatogram = chromatograms[chrom_name]
            else:
                # Multiple chromatograms available, let the user choose
                chrom_name = tk.StringVar()
                dialog = tk.Toplevel(self.root)
                dialog.title("Select Chromatogram")
                dialog.geometry("300x200")
                dialog.grab_set()  # Make the dialog modal
                
                ttk.Label(dialog, text="Select a chromatogram:").pack(pady=10)
                
                # Create a listbox with all chromatogram names
                listbox = tk.Listbox(dialog)
                listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
                
                for name in chromatograms.keys():
                    listbox.insert(tk.END, name)
                
                # Select the first item by default
                listbox.selection_set(0)
                
                # Button to confirm selection
                def on_select():
                    selection = listbox.curselection()
                    if selection:
                        chrom_name.set(listbox.get(selection[0]))
                        dialog.destroy()
                
                ttk.Button(dialog, text="Select", command=on_select).pack(pady=10)
                
                # Wait for the dialog to be closed
                self.root.wait_window(dialog)
                
                # If user selected a chromatogram, use it
                if chrom_name.get():
                    self.chromatogram = chromatograms[chrom_name.get()]
                else:
                    return
            
            # Update UI to show the selected chromatogram
            self.chrom_name_label.config(text=f"Loaded: {self.chromatogram.name}")
            
            # Initialize an empty mask
            self._create_new_mask()
            
            # Display the chromatogram
            self._display_chromatogram()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to use workspace chromatogram: {str(e)}")
            import traceback
            traceback.print_exc()


def run_mask_creator():
    """
    Run the MaskCreatorGUI application
    """
    root = tk.Tk()
    app = MaskCreatorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_mask_creator()
