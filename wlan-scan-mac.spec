# WLAN Scan (macOS)
#
# PyInstaller build:  python -m PyInstaller wlan-scan-mac.spec
# Requires: pip install pyinstaller  (run ON a Mac)
# Output:  dist/WLAN Scan.app

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
    icon='wlan_scan.icns',
)

app = BUNDLE(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='WLAN Scan.app',
    icon='wlan_scan.icns',
    bundle_identifier='io.wlanscan.app',
    info_plist={
        'CFBundleName': 'WLAN Scan',
        'CFBundleDisplayName': 'WLAN Scan',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
        # Wi-Fi scanning on macOS is location-gated (10.15+); without this
        # key CoreWLAN returns no results.
        'NSLocationUsageDescription': 'WLAN Scan needs location access to '
                                      'scan for nearby Wi-Fi networks.',
        'NSLocationWhenInUseUsageDescription': 'WLAN Scan needs location '
                                              'access to scan for nearby '
                                              'Wi-Fi networks.',
    },
)
