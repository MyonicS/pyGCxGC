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


def main(argv=None):
    """Command line entry point (``pygcxgc-maskcreator``): starts the app (spec NB-5)."""
    import argparse

    parser = argparse.ArgumentParser(description="pyGCxGC mask creator (deprecated)")
    parser.add_argument(
        "--legacy", action="store_true", help="the deprecated Tk mask creator"
    )
    args = parser.parse_args(argv)
    if not args.legacy:
        from pyGCxGC.app.__main__ import replaces_legacy_tool

        code = replaces_legacy_tool("pygcxgc-maskcreator", [])
        if code is not None:
            return code
    launch_mask_creator()
    return 0
