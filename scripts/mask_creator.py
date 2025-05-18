#!/usr/bin/env python
"""
Command-line script for launching the pyGCxGC mask creator GUI.
"""

import argparse
import sys
from pyGCxGC.mask_gui import launch_mask_creator


def main():
    """Main entry point for the mask creator GUI."""
    parser = argparse.ArgumentParser(description="pyGCxGC Mask Creator GUI")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    
    args = parser.parse_args()
    
    if args.version:
        import pyGCxGC
        print(f"pyGCxGC version {pyGCxGC.__version__}")
        return 0
    
    # Launch the GUI
    launch_mask_creator()
    return 0


if __name__ == "__main__":
    sys.exit(main())
