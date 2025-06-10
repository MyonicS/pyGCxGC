"""
Mask GUI module - provides functions to launch the mask creator GUI.
"""

import tkinter as tk
import tkinter.font as tkfont
from .gui.mask_creator import MaskCreatorGUI


def launch_mask_creator():
    """
    Launch the mask creator GUI.
    
    This function creates a new Tkinter window and starts the mask creator GUI application.
    
    Examples
    --------
    >>> import pyGCxGC as gcgc
    >>> gcgc.launch_mask_creator()
    """
    root = tk.Tk()
    
    # Configure Liberation Sans font for all widgets
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family="Liberation Sans")
    
    text_font = tkfont.nametofont("TkTextFont")
    text_font.configure(family="Liberation Sans")
    
    fixed_font = tkfont.nametofont("TkFixedFont")
    fixed_font.configure(family="Liberation Sans")
    
    app = MaskCreatorGUI(root)
    root.mainloop()
    return None