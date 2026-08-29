# WLAN Scan — single-file portable build (for remote deployment tools)
#
# Produces dist/WLAN-Scan-Portable.exe — one self-contained file.
# Requires NO _internal folder, no install, no dependencies.

# -*- mode: python ; coding: utf-8 -*-

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
        'wlan_scan.connection',
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
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='WLAN-Scan-Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='wlan_scan.ico',
    disable_windowed_traceback=True,
)
