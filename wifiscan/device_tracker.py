"""wifiscan.device_tracker — per-device aggregation with TTL aging.

The firmware's promiscuous-mode sniffer (``src/main.cpp`` ``snifferCallback``)
emits one CSV row per sniffed probe-request frame, each tagged with
``frame_type=probe_req`` and the client's source MAC in ``src_mac``.  A
chatty client produces dozens of near-duplicate rows per second, so a
downstream viewer needs to collapse repeated sightings of the same MAC
into a single tracked device and age out devices that have gone quiet.

This module does that aggregation on the host side (where we have heap
and Python) rather than on the firmware.  It is pure-Python and
I/O-free: it ingests parsed row dicts (as produced by
:func:`wifiscan.schema.parse_data_row`) and exposes a
:meth:`DeviceTracker.current_devices` snapshot suitable for a live
dashboard or offline analysis.

The clock domain is the firmware's ``millis()`` value, carried in each
row's ``timestamp_ms`` field.  A device is considered active while the
most recently seen ``timestamp_ms`` minus the device's ``last_seen`` is
within ``ttl_ms`` (default 10 000 ms, mirroring the reference
Wifi-Radar-Scanner-for-ESP32 project).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

__all__ = ["DeviceInfo", "DeviceTracker", "DEFAULT_TTL_MS"]


#: Default aging window in milliseconds (matches the reference project).
DEFAULT_TTL_MS: int = 10_000


@dataclass(frozen=True)
class DeviceInfo:
    """Snapshot of a single tracked Wi-Fi device (probe-request source)."""

    mac: str
    rssi: int
    last_seen: int          # firmware millis() of the most recent sighting
    ssid: str
    channel: int
    est_distance_m: float

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict representation."""
        return asdict(self)


class DeviceTracker:
    """Aggregate probe-request rows by MAC, aging out stale devices.

    Call :meth:`update` for every parsed row (both ``ap`` and
    ``probe_req`` rows are accepted; only ``probe_req`` rows are tracked).
    Call :meth:`current_devices` to get the active set sorted by RSSI
    descending.
    """

    def __init__(self, ttl_ms: int = DEFAULT_TTL_MS) -> None:
        self._ttl_ms = ttl_ms
        # mac -> [rssi, last_seen, ssid, channel, est_distance_m]
        self._devices: dict[str, list] = {}
        # Highest timestamp_ms seen so far — the "current time" in the
        # firmware's clock domain, used for aging.
        self._clock: int = 0

    def update(self, row: dict) -> None:
        """Ingest one parsed row.

        ``probe_req`` rows update (or insert) the device keyed by
        ``src_mac``.  All other row types (``ap``, etc.) are ignored —
        they pass through the capture pipeline untouched; this tracker
        only concerns itself with client devices seen via promiscuous
        mode.
        """
        if row.get("frame_type") != "probe_req":
            return

        mac = row.get("src_mac", "")
        if not mac:
            return

        try:
            ts = int(row["timestamp_ms"])
            rssi = int(row["rssi"])
            channel = int(row["channel"]) if row.get("channel") else 0
            dist = float(row["est_distance_m"]) if row.get("est_distance_m") else 0.0
        except (ValueError, KeyError):
            return

        if ts > self._clock:
            self._clock = ts

        ssid = row.get("ssid", "")
        existing = self._devices.get(mac)
        if existing is not None:
            # Keep the highest timestamp seen for this MAC (rows may
            # arrive slightly out of order); refresh signal data from
            # the most recent sighting.
            last_seen = max(existing[1], ts)
        else:
            last_seen = ts
        self._devices[mac] = [rssi, last_seen, ssid, channel, dist]
        self._prune()

    def _prune(self) -> None:
        """Remove devices whose last_seen is older than ``ttl_ms``."""
        if self._clock == 0:
            return
        cutoff = self._clock - self._ttl_ms
        stale = [mac for mac, info in self._devices.items() if info[1] < cutoff]
        for mac in stale:
            del self._devices[mac]

    def current_devices(self) -> list[DeviceInfo]:
        """Return active devices sorted by RSSI descending (strongest first).

        Stale devices are pruned before returning.  An empty list means
        no probe-request sources have been seen within the TTL window.
        """
        self._prune()
        devices = [
            DeviceInfo(
                mac=mac,
                rssi=info[0],
                last_seen=info[1],
                ssid=info[2],
                channel=info[3],
                est_distance_m=info[4],
            )
            for mac, info in self._devices.items()
        ]
        devices.sort(key=lambda d: d.rssi, reverse=True)
        return devices

    def clear(self) -> None:
        """Remove all tracked devices and reset the clock."""
        self._devices.clear()
        self._clock = 0
