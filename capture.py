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
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import serial
except ImportError as exc:
    raise SystemExit(
        "pyserial is required. Install with: pip install pyserial"
    ) from exc

# Canonical CSV schema — column list, header string, row/footer parsers —
# lives in the wifiscan package so capture.py and heatmap.py agree.
from wifiscan.schema import (
    EXPECTED_COLUMNS,
    HEADER_LINE,
    SCHEMA_VERSION,
    parse_data_row,
    parse_footer,
    parse_schema_version,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_PORT = "COM10"
DEFAULT_BAUD = 115200
TIMEOUT_S = 1.0

OUTPUT_DIR = Path(__file__).parent / "logs"
OUTPUT_DIR.mkdir(exist_ok=True)


def timestamped_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / f"signal_map_{ts}.csv"


def _tmp_path(path: Path) -> Path:
    """Sibling tmp file path used for atomic writes (e.g. `out.csv` -> `out.csv.tmp`)."""
    return path.with_suffix(path.suffix + ".tmp")


def _cleanup_tmp(tmp: Path) -> None:
    """Best-effort delete of a tmp file; never raises."""
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def write_header(path: Path) -> None:
    """Atomically write the CSV header.

    Writes to ``path.with_suffix(path.suffix + ".tmp")`` first, then uses
    ``os.replace`` to swap. On any exception, the tmp file is best-effort
    cleaned up and the original error is re-raised. After a successful
    call, no ``.tmp`` file is left behind.
    """
    tmp = _tmp_path(path)
    try:
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(EXPECTED_COLUMNS))
            writer.writeheader()
        os.replace(tmp, path)
    except Exception:
        _cleanup_tmp(tmp)
        raise


def append_rows(path: Path, rows: list[dict]) -> None:
    """Append rows to an existing CSV file.

    The header write (see :func:`write_header`) is fully atomic via
    tmp+rename. Subsequent appends use plain append mode, which is atomic
    enough for our scale (small writes, single writer, no concurrent
    readers) and avoids the quadratic cost of rewriting the whole file on
    every spot.
    """
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(EXPECTED_COLUMNS))
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def version_check_message(fw_version: int | None,
                          expected: int = SCHEMA_VERSION) -> str:
    """Build the log line for the firmware/Python schema-version handshake.

    * ``fw_version is None`` — no ``# schema_version=N`` line was seen
      before the column header (legacy firmware); emit a one-line warning.
    * ``fw_version == expected`` — emit an OK line.
    * otherwise — emit a WARNING (mismatch) but do NOT instruct the caller
      to abort; capture continues so data is not lost on a minor drift.
    """
    if fw_version is None:
        return (f"[capture] WARNING: no schema_version line seen "
                f"(expected {expected}); assuming legacy firmware.")
    if fw_version == expected:
        return f"[capture] schema_version={fw_version}"
    return (f"[capture] WARNING: schema_version mismatch — firmware "
            f"reported {fw_version}, capture.py expects {expected}; "
            f"continuing anyway.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=DEFAULT_PORT,
                        help=f"Serial port (default {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                        help=f"Baud rate (default {DEFAULT_BAUD})")
    parser.add_argument("--live", action="store_true",
                        help="Serve a real-time radar dashboard over HTTP "
                             "(issue #27).")
    parser.add_argument("--web-port", type=int, default=8080,
                        help="HTTP port for the --live dashboard (default 8080)")
    args = parser.parse_args()

    print(f"Opening {args.port} at {args.baud} baud...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=TIMEOUT_S)
    except serial.SerialException as exc:
        raise SystemExit(f"Could not open {args.port}: {exc}") from exc

    print("Connected. Type labels into the monitor, press BOOT to log a spot.")
    print("Press Ctrl+C to stop.\n")

    # Live radar dashboard (issue #27). Started after the serial
    # connection is established so a port conflict is surfaced before
    # the web server binds. Pure stdlib — no extra dependencies.
    live_server = None
    if args.live:
        from wifiscan.live import LiveServer
        live_server = LiveServer(port=args.web_port)
        live_server.start()
        print(f"[capture] live radar dashboard: {live_server.url}\n")

    header_seen = False
    fw_schema_version: int | None = None
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

            # Pre-header boot handshake: capture the firmware's advertised
            # schema_version line (printed immediately before the column
            # header). A missing line is handled after the header arrives.
            if not header_seen and fw_schema_version is None:
                v = parse_schema_version(line)
                if v is not None:
                    fw_schema_version = v

            # Header validation (non-gating: data rows are processed
            # even if the board was already running and the header was
            # missed).
            if not header_seen:
                if line == HEADER_LINE:
                    print("[capture] header OK — est_distance_m present")
                    print(version_check_message(fw_schema_version))
                    header_seen = True
                    continue

            # Footer marks the end of one spot's rows. Flush to disk.
            if parse_footer(line) is not None:
                if buffer:
                    if current_path is None:
                        current_path = timestamped_path()
                        write_header(current_path)
                    append_rows(current_path, buffer)
                    print(f"[capture] wrote {len(buffer)} row(s) -> "
                          f"{current_path.name}")
                    all_rows.extend(buffer)
                    buffer = []
                    spot_index += 1
                continue

            # Regular CSV data row.
            row = parse_data_row(line)
            if row is not None:
                buffer.append(row)
                if live_server is not None:
                    live_server.broadcast(row)

    except KeyboardInterrupt:
        print("\n[capture] Ctrl+C — stopping.")
        # Flush any partial buffer. Append (do not overwrite) so partial
        # captures don't lose the already-written spot data; missing the
        # header row is acceptable — heatmap.py will surface it via the
        # required-column check.
        if buffer:
            if current_path is None:
                current_path = timestamped_path()
                write_header(current_path)
            append_rows(current_path, buffer)
            print(f"[capture] flushed {len(buffer)} partial row(s) -> "
                  f"{current_path.name}")
            all_rows.extend(buffer)

        try:
            ser.close()
        except Exception:
            pass

        if live_server is not None:
            live_server.stop()
            print("[capture] live dashboard stopped.")

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
