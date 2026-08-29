# WLAN Scan
#
# PyInstaller build:  python -m PyInstaller wlan-scan.spec
#
# Requires: pip install pyinstaller
# Output:  dist/WLAN Scan.exe  (~60-80 MB, portable, no install needed)

# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('wlan_scan.ico', '.')],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'wlan_scan',
        'wlan_scan.models',
        'wlan_scan.scanner',
        'wlan_scan.ie_parser',
        'wlan_scan.vendor_lookup',
        'wlan_scan.netsh_scanner',
        'wlan_scan.pwsh_scanner',
        'wlan_scan.macos_scanner',
        'wlan_scan.airport_scanner',
        'wlan_scan.corewlan_scanner',
        'wlan_scan.oui_lookup',
        'wlan_scan.logging_setup',
        'wlan_scan.gui',
        'wlan_scan.gui.main_window',
        'wlan_scan.gui.network_table',
        'wlan_scan.gui.channel_map',
        'wlan_scan.gui.signal_graph',
        'wlan_scan.gui.detail_panel',
        'wlan_scan.gui.bssid_tracker',
        'wlan_scan.gui.ping_dialog',
        'wlan_scan.gui.neighbor_dialog',
        'wlan_scan.gui.speedtest_dialog',
        'wlan_scan.gui.styles',
        'wlan_scan.updater',
        'wlan_scan.version',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'tornado',
        'flask',
        'django',
        'sqlalchemy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WLAN Scan',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    icon='wlan_scan.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='WLAN Scan',
)
