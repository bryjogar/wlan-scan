"""macOS build helper — creates a portable .app bundle with PyInstaller.

Usage (ON a Mac):
    pip install -r requirements.txt pyinstaller
    python build_mac.py

Output:  dist/WLAN Scan.app  +  dist/WLAN-Scan-mac.zip

Gatekeeper: unsigned apps built on your Mac run there, but other Macs
show "developer cannot be verified" on first launch (right-click → Open).
For distribution beyond the team, sign + notarize (see end of file).
"""

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def embed_version():
    """Write current git SHA to version.py so it's bundled into the app."""
    sha = "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_DIR,
            timeout=5,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
    except Exception:
        pass

    version_file = PROJECT_DIR / "wlan_scan" / "version.py"
    version_file.write_text(
        f'# Auto-generated at build time by build_mac.py\n'
        f'__version_sha__ = "{sha}"\n'
        f'__build_time__ = "{datetime.now().isoformat()}"\n'
    )
    print(f"  Version SHA: {sha[:7]} ({sha})")
    return sha


def main():
    print("=" * 60)
    print("WLAN Scan - macOS portable build")
    print(f"  Platform: {sys.platform}")
    print(f"  Python:   {sys.version.split()[0]}")
    print("=" * 60)

    if sys.platform != "darwin":
        print("ERROR: this script must run on macOS (PyInstaller can't cross-build).")
        sys.exit(1)

    spec = PROJECT_DIR / "wlan-scan-mac.spec"
    if not spec.exists():
        print(f"ERROR: {spec} not found")
        sys.exit(1)

    print("\n[1/3] Embedding version info...")
    embed_version()

    print("\n[2/3] Cleaning previous build...")
    for d in ["build", "dist"]:
        p = PROJECT_DIR / d
        if p.exists():
            shutil.rmtree(p)
            print(f"  Removed {d}/")

    print("\n[3/3] Running PyInstaller...")
    cmd = [sys.executable, "-m", "PyInstaller", str(spec)]
    result = subprocess.run(cmd, cwd=PROJECT_DIR)

    if result.returncode != 0:
        print("\nBuild FAILED. See output above for errors.")
        sys.exit(1)

    app_dir = PROJECT_DIR / "dist" / "WLAN Scan.app"
    if not app_dir.exists():
        print("\nERROR: expected 'WLAN Scan.app' but it wasn't produced.")
        sys.exit(1)

    size_mb = sum(f.stat().st_size for f in app_dir.rglob('*') if f.is_file()) / (1024 * 1024)
    zip_path = PROJECT_DIR / "dist" / "WLAN-Scan-mac.zip"
    print(f"\nDONE - {size_mb:.0f} MB bundle")
    print(f"  {app_dir}")
    print("  Creating distributable zip...")
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", PROJECT_DIR / "dist", "WLAN Scan.app")
    print(f"  {zip_path}")
    print("\nDistribute the zip. Recipient unzips and drags WLAN Scan.app to")
    print("Applications, or runs it from the unzipped folder.")


if __name__ == "__main__":
    main()
