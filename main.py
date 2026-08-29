"""WLAN Scan — Professional Wi-Fi network analysis tool.

Usage:
    python main.py              # Launch the GUI
    python main.py --cli        # Command-line scan (no GUI)
    python main.py --export     # Scan and export CSV (no GUI)
"""

import sys
import os


def main():
    if "--cli" in sys.argv or "--export" in sys.argv:
        run_cli()
        return

    run_gui()


def run_gui():
    from PySide6.QtWidgets import QApplication
    from wlan_scan.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("WLAN Scan")
    app.setOrganizationName("WLANScan")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


def run_cli():
    """Command-line scan mode."""
    import json

    if sys.platform == "darwin":
        from wlan_scan.macos_scanner import MacScanner
        result = MacScanner().scan()
    elif sys.platform == "win32":
        from wlan_scan.scanner import scan
        result = scan()
    else:
        print("Error: Wi-Fi scanning is only available on Windows or macOS.")
        sys.exit(1)

    if "--export" in sys.argv:
        import csv
        from datetime import datetime
        path = f"wifi_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "SSID", "BSSID", "RSSI", "Channel", "Band", "Channel Width",
                "Security", "PHY", "Vendor", "Max Rate", "Utilization", "Clients",
            ])
            for net in result.networks:
                for bss in net.bss_list:
                    writer.writerow([
                        net.ssid, bss.bssid, bss.rssi, bss.channel, bss.band,
                        bss.channel_width, bss.security, bss.phy_type, bss.vendor,
                        f"{bss.max_rate:.0f}",
                        f"{bss.channel_utilization:.1f}" if bss.channel_utilization else "",
                        bss.station_count if bss.station_count else "",
                    ])
        print(f"Exported to: {path}")

    # Print summary
    total_bss = sum(len(n.bss_list) for n in result.networks)
    print(f"\n📡 {result.interface_name}")
    print(f"   Networks: {len(result.networks)}  |  BSS entries: {total_bss}\n")
    print(f"{'SSID':<25} {'BSSID':<18} {'RSSI':>5} {'Ch':>3} {'Band':>8} {'Security':<12} {'PHY':<18} {'Vendor'}")
    print("-" * 140)
    for net in result.networks:
        for bss in net.bss_list:
            print(
                f"{net.ssid[:24]:<25} {bss.bssid:<18} {bss.rssi:>4} dBm "
                f"{bss.channel:>3} {bss.band:>8} {bss.security:<12} "
                f"{bss.phy_type:<18} {bss.vendor[:30]}"
            )


if __name__ == "__main__":
    main()
