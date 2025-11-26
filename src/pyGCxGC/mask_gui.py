"""
Mask GUI module - provides functions to launch the mask creator GUI.
"""

import tkinter as tk
import tkinter.font as tkfont
import platform
from .gui.mask_creator import MaskCreatorGUI


def _get_system_font():
    """
    Get appropriate font based on operating system for Tkinter widgets.

    Returns
    -------
    str
        Font family name for the current system
    """
    system = platform.system()
    if system == "Windows":
        return "Arial"
    elif system == "Linux":
        return "Liberation Sans"
    elif system == "Darwin":  # macOS
        return "Helvetica"
    else:
        return "sans-serif"


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

    # Configure system-appropriate font for all widgets
    system_font = _get_system_font()

    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family=system_font)

    text_font = tkfont.nametofont("TkTextFont")
    text_font.configure(family=system_font)

    fixed_font = tkfont.nametofont("TkFixedFont")
    fixed_font.configure(family=system_font)

    app = MaskCreatorGUI(root)
    root.mainloop()
    return None
