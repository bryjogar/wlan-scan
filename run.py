"""WLAN Scan — entry point for PyInstaller / direct run."""

import sys
import os

# Ensure the package is importable when running as a bundled exe
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle — add the exe dir to path
    bundle_dir = os.path.dirname(sys.executable)
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

from wlan_scan.gui.main_window import main

if __name__ == "__main__":
    main()
