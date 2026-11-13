"""WLAN Scan — Professional Wi-Fi network analysis tool.

Usage:
    python main.py              # Launch the GUI
    python main.py --cli        # Command-line scan (no GUI)
    python main.py --export     # Scan and export CSV (no GUI)
    python main.py --record F   # Also append every scan to F (JSON lines)
    python main.py --replay F   # Play back a recording instead of scanning
"""

import argparse
import sys


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="wlan-scan", description="WLAN Scan")
    p.add_argument("--cli", action="store_true", help="command-line scan (no GUI)")
    p.add_argument("--export", action="store_true", help="scan and export CSV (no GUI)")
    p.add_argument("--record", metavar="FILE",
                   help="append every scan to FILE as JSON lines")
    p.add_argument("--replay", metavar="FILE",
                   help="play back a recording instead of scanning")
    # Qt may add its own arguments; ignore anything we don't know.
    args, _unknown = p.parse_known_args(argv)
    return args


def main():
    args = parse_args()
    if args.cli or args.export:
        run_cli(args)
        return

    run_gui(args)


def run_gui(args):
    from PySide6.QtWidgets import QApplication
    from wlan_scan.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("WLAN Scan")
    app.setOrganizationName("WLANScan")

    window = MainWindow(replay=args.replay, record=args.record)
    window.show()

    sys.exit(app.exec())


def run_cli(args):
    """Command-line scan mode."""
    from wlan_scan.backends import candidate_backends, open_first
    from wlan_scan.recording import ScanRecorder

    backend, _ = open_first(candidate_backends(replay=args.replay),
                            debug=lambda m: print(m, file=sys.stderr))
    if backend is None:
        print("Error: no Wi-Fi scanner backend is available on this system "
              "(Windows and macOS are supported; use --replay FILE elsewhere).")
        sys.exit(1)
    try:
        result = backend.scan()
    finally:
        backend.close()
    if args.record:
        rec = ScanRecorder(args.record)
        rec.write(result)
        rec.close()

    if args.export:
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
