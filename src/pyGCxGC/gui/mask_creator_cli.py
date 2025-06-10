#!/usr/bin/env python
"""
Command-line script to launch the pyGCxGC Mask Creator GUI.
"""

import sys
import argparse
import tkinter as tk
from pyGCxGC.gui.mask_creator import MaskCreatorGUI


def main():
    """
    Main entry point for the mask creator GUI command-line script.
    """
    parser = argparse.ArgumentParser(description="Launch the pyGCxGC Mask Creator GUI")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    
    args = parser.parse_args()
    
    if args.version:
        import pyGCxGC
        print(f"pyGCxGC version {pyGCxGC.__version__}")
        return 0
    
    # Launch the GUI
    root = tk.Tk()
    app = MaskCreatorGUI(root)
    root.mainloop()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
