#!/usr/bin/env python3
"""
capture.py — live serial logger for the Wi-Fi Scanner with Signal Map.

Reads CSV rows from the ESP32 over USB serial, buffers them until the
firmware prints its `# spot=<id> ...` footer comment, then flushes the
buffer to a timestamped file under `logs/`. Compatible with the firmware's
header schema, including the `est_distance_m` column.

Usage:
    python capture.py                # live capture, Ctrl+C to stop
    python capture.py --port COM11   # override port

Dependencies:
    pip install pyserial
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import serial
except ImportError as exc:
    raise SystemExit(
        "pyserial is required. Install with: pip install pyserial"
    ) from exc


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_PORT = "COM10"
DEFAULT_BAUD = 115200
TIMEOUT_S = 1.0

OUTPUT_DIR = Path(__file__).parent / "logs"
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
HEADER_RE = re.compile(
    r"^#\s*spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,"
    r"auth_mode,est_distance_m\s*$"
)
SPOT_FOOTER_RE = re.compile(r"^#\s*spot=(\d+)")


def timestamped_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / f"signal_map_{ts}.csv"


def write_rows(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "spot_id", "spot_label", "timestamp_ms", "ssid", "bssid",
        "rssi", "channel", "auth_mode", "est_distance_m",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_row(line: str) -> dict | None:
    """Parse a CSV data row produced by the firmware into a dict."""
    parts = next(csv.reader([line]))
    if len(parts) != 9:
        return None
    return {
        "spot_id": parts[0],
        "spot_label": parts[1],
        "timestamp_ms": parts[2],
        "ssid": parts[3],
        "bssid": parts[4],
        "rssi": parts[5],
        "channel": parts[6],
        "auth_mode": parts[7],
        "est_distance_m": parts[8],
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=DEFAULT_PORT,
                        help=f"Serial port (default {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                        help=f"Baud rate (default {DEFAULT_BAUD})")
    args = parser.parse_args()

    print(f"Opening {args.port} at {args.baud} baud...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=TIMEOUT_S)
    except serial.SerialException as exc:
        raise SystemExit(f"Could not open {args.port}: {exc}") from exc

    print("Connected. Type labels into the monitor, press BOOT to log a spot.")
    print("Press Ctrl+C to stop.\n")

    header_seen = False
    buffer: list[dict] = []
    all_rows: list[dict] = []
    current_path: Path | None = None
    spot_index = 0

    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            print(line)

            # Skip empty lines.
            if not line:
                continue

            # Header validation.
            if not header_seen:
                if HEADER_RE.match(line):
                    print("[capture] header OK — est_distance_m present")
                    header_seen = True
                continue

            # Footer marks the end of one spot's rows. Flush to disk.
            if SPOT_FOOTER_RE.match(line):
                if buffer:
                    if current_path is None:
                        current_path = timestamped_path()
                    write_rows(current_path, buffer)
                    print(f"[capture] wrote {len(buffer)} row(s) -> "
                          f"{current_path.name}")
                    all_rows.extend(buffer)
                    buffer = []
                    spot_index += 1
                continue

            # Regular CSV data row.
            row = parse_row(line)
            if row is not None:
                buffer.append(row)

    except KeyboardInterrupt:
        print("\n[capture] Ctrl+C — stopping.")
    finally:
        # Flush any partial buffer.
        if buffer:
            if current_path is None:
                current_path = timestamped_path()
            write_rows(current_path, buffer)
            print(f"[capture] flushed {len(buffer)} partial row(s) -> "
                  f"{current_path.name}")
            all_rows.extend(buffer)

        try:
            ser.close()
        except Exception:
            pass

        if not header_seen:
            print("[capture] WARNING: never saw the firmware header. "
                  "Is the sketch flashed and running?")
        elif all_rows:
            print(f"\n[capture] captured {len(all_rows)} row(s) across "
                  f"{spot_index} spot(s)")
        else:
            print("[capture] no spots captured.")


if __name__ == "__main__":
    main()
