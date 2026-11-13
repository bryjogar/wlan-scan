"""Windows build helper — creates a portable app bundle with PyInstaller.

Usage (on Windows — x64 or ARM64):
    pip install -r requirements.txt
    python build_exe.py

Output:  dist/WLAN Scan/WLAN Scan.exe  (~80 MB, self-contained)
         No install needed — run directly from the folder.

Why onedir instead of onefile:
    Single EXE decompresses 80 MB to a temp directory on every launch,
    adding 5-15 seconds of startup time.  onedir extracts once at build
    time and starts instantly on every subsequent run.
"""

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
    print("WLAN Scan - Windows portable build")
    print(f"  Platform: {sys.platform}")
    print(f"  Python:   {sys.version.split()[0]}")
    print("=" * 60)

    spec = PROJECT_DIR / "wlan-scan.spec"
    if not spec.exists():
        print(f"ERROR: {spec} not found")
        sys.exit(1)

    print("\n[1/3] Embedding version info...")
    embed_version()
    update_oui_snapshot()

    print("\n[2/3] Cleaning previous build...")
    for d in ["build", "dist"]:
        p = PROJECT_DIR / d
        if p.exists():
            import shutil
            shutil.rmtree(p)
            print(f"  Removed {d}/")

    print("\n[3/3] Running PyInstaller...")
    cmd = [sys.executable, "-m", "PyInstaller", str(spec)]
    result = subprocess.run(cmd, cwd=PROJECT_DIR)

    if result.returncode != 0:
        print("\nBuild FAILED. See output above for errors.")
        sys.exit(1)

    exe_dir = PROJECT_DIR / "dist" / "WLAN Scan"
    exe_path = exe_dir / "WLAN Scan.exe"
    size_mb = sum(f.stat().st_size for f in exe_dir.rglob('*') if f.is_file()) / (1024 * 1024) if exe_dir.exists() else 0
    print(f"\nDONE - {size_mb:.0f} MB folder")
    print(f"  {exe_path}")
    print("\nDistribute the entire 'WLAN Scan' folder, or zip it for sharing.")
    print("Startup is instant - no decompression on every launch.")


if __name__ == "__main__":
    main()
