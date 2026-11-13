"""WLAN Scan single-file portable build (for remote deployment tools).

Usage (Windows):
    python build_portable.py

Output:  dist/WLAN-Scan-Portable.exe  (~50-60 MB, ONE file, no _internal)
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def embed_version():
    """Write the version (from the release tag) into wlan_scan/version.py."""
    from tools.versioning import write_version_file

    return write_version_file()


def update_oui_snapshot():
    """Refresh the bundled IEEE OUI list; keep the committed one on failure."""
    script = PROJECT_DIR / "tools" / "build_oui.py"
    r = subprocess.run([sys.executable, str(script)], cwd=PROJECT_DIR)
    if r.returncode != 0:
        print("  OUI refresh failed - using the committed snapshot")


def main():
    print("=" * 60)
    print("WLAN Scan - single-file portable build")
    print(f"  Platform: {sys.platform}")
    print(f"  Python:   {sys.version.split()[0]}")
    print("=" * 60)

    if sys.platform != "win32":
        print("ERROR: this script must run on Windows (PyInstaller can't cross-build).")
        sys.exit(1)

    spec = PROJECT_DIR / "wlan-scan-onefile.spec"
    if not spec.exists():
        print(f"ERROR: {spec} not found")
        sys.exit(1)

    print("\n[1/3] Embedding version info...")
    embed_version()
    update_oui_snapshot()

    print("\n[2/3] Cleaning previous build...")
    build_dir = PROJECT_DIR / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f"  Removed build/")
    # NOTE: dist/ is NOT cleaned here — a prior step (build_exe.py) may have
    # placed the folder build + zip there; we only add our single exe.

    print("\n[3/3] Running PyInstaller (onefile)...")
    cmd = [sys.executable, "-m", "PyInstaller", str(spec), "--noconfirm"]
    result = subprocess.run(cmd, cwd=PROJECT_DIR)

    if result.returncode != 0:
        print("\nBuild FAILED. See output above for errors.")
        sys.exit(1)

    exe_path = PROJECT_DIR / "dist" / "WLAN-Scan-Portable.exe"
    if not exe_path.exists():
        print("\nERROR: expected WLAN-Scan-Portable.exe but it wasn't produced.")
        sys.exit(1)

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"\nDONE - {size_mb:.0f} MB single file")
    print(f"  {exe_path}")
    print("\nPush this ONE file to any Windows device. No _internal folder,")
    print("no install, no dependencies. Startup takes a few seconds longer.")


if __name__ == "__main__":
    main()
