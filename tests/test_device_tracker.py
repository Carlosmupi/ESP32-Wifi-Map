"""pytest suite for wifiscan.device_tracker (issue #28).

Covers:
* Deduplication of repeated probe requests from the same MAC.
* RSSI update on resighting (most recent RSSI wins).
* TTL-based aging: a device that stops transmitting is dropped.
* Pass-through of ``ap`` rows (tracker ignores them, no state change).
* Sorting of current_devices by RSSI descending.
* Graceful handling of malformed rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from wifiscan.device_tracker import DeviceTracker, DeviceInfo, DEFAULT_TTL_MS  # noqa: E402


def _probe_row(
    *,
    src_mac: str = "aa:bb:cc:dd:ee:ff",
    rssi: str = "-55",
    timestamp_ms: str = "10000",
    ssid: str = "HomeNet",
    channel: str = "6",
    est_distance_m: str = "2.34",
    frame_type: str = "probe_req",
) -> dict:
    """Build a parsed-row dict mimicking wifiscan.schema.parse_data_row output."""
    return {
        "spot_id": "1",
        "spot_label": "lab",
        "timestamp_ms": timestamp_ms,
        "ssid": ssid,
        "bssid": "",
        "rssi": rssi,
        "channel": channel,
        "auth_mode": "",
        "est_distance_m": est_distance_m,
        "frame_type": frame_type,
        "src_mac": src_mac,
    }


def _ap_row(*, rssi: str = "-60", bssid: str = "11:22:33:44:55:66",
            timestamp_ms: str = "10000") -> dict:
    """Build a parsed AP row (frame_type=ap, no src_mac)."""
    row = _probe_row(frame_type="ap", rssi=rssi, timestamp_ms=timestamp_ms)
    row["src_mac"] = ""
    row["bssid"] = bssid
    return row


# ---------------------------------------------------------------------------
# Dedup + RSSI update
# ---------------------------------------------------------------------------

def test_repeated_probes_collapse_to_one_device() -> None:
    """Multiple sightings of the same MAC produce a single tracked device."""
    tracker = DeviceTracker()
    for i in range(10):
        tracker.update(_probe_row(rssi="-55", timestamp_ms=str(10000 + i * 100)))
    devices = tracker.current_devices()
    assert len(devices) == 1
    assert devices[0].mac == "aa:bb:cc:dd:ee:ff"


def test_rssi_updates_to_most_recent() -> None:
    """A resighting updates the stored RSSI to the latest value."""
    tracker = DeviceTracker()
    tracker.update(_probe_row(rssi="-55", timestamp_ms="10000"))
    tracker.update(_probe_row(rssi="-70", timestamp_ms="10500"))
    devices = tracker.current_devices()
    assert len(devices) == 1
    assert devices[0].rssi == -70
    assert devices[0].last_seen == 10500


def test_last_seen_tracks_latest_timestamp() -> None:
    """last_seen reflects the highest timestamp_ms seen for that MAC."""
    tracker = DeviceTracker()
    tracker.update(_probe_row(timestamp_ms="10000"))
    tracker.update(_probe_row(timestamp_ms="12000"))
    tracker.update(_probe_row(timestamp_ms="11000"))
    devices = tracker.current_devices()
    assert devices[0].last_seen == 12000


# ---------------------------------------------------------------------------
# TTL aging
# ---------------------------------------------------------------------------

def test_device_dropped_after_ttl() -> None:
    """A device not seen within the TTL window is removed."""
    tracker = DeviceTracker(ttl_ms=5000)
    tracker.update(_probe_row(timestamp_ms="10000"))
    assert len(tracker.current_devices()) == 1

    # A different, newer device advances the clock past the first's TTL.
    tracker.update(_probe_row(src_mac="11:22:33:44:55:66",
                              timestamp_ms="20000"))
    devices = tracker.current_devices()
    macs = [d.mac for d in devices]
    assert "aa:bb:cc:dd:ee:ff" not in macs
    assert "11:22:33:44:55:66" in macs


def test_device_survives_within_ttl() -> None:
    """A device seen just inside the TTL window is retained."""
    tracker = DeviceTracker(ttl_ms=10000)
    tracker.update(_probe_row(timestamp_ms="10000"))
    tracker.update(_probe_row(src_mac="11:22:33:44:55:66",
                              timestamp_ms="19999"))
    devices = tracker.current_devices()
    assert len(devices) == 2


def test_default_ttl_is_10_seconds() -> None:
    assert DEFAULT_TTL_MS == 10_000


# ---------------------------------------------------------------------------
# AP row pass-through
# ---------------------------------------------------------------------------

def test_ap_rows_are_ignored() -> None:
    """AP rows do not create or modify tracked devices."""
    tracker = DeviceTracker()
    tracker.update(_ap_row())
    assert tracker.current_devices() == []


def test_ap_rows_do_not_affect_existing_devices() -> None:
    """Feeding an AP row after a probe_req does not alter the tracked device."""
    tracker = DeviceTracker()
    tracker.update(_probe_row(rssi="-55", timestamp_ms="10000"))
    tracker.update(_ap_row(rssi="-90", timestamp_ms="10001"))
    devices = tracker.current_devices()
    assert len(devices) == 1
    assert devices[0].rssi == -55


def test_ap_rows_do_not_advance_clock() -> None:
    """AP rows should not advance the aging clock (only probe_req rows do)."""
    tracker = DeviceTracker(ttl_ms=1000)
    tracker.update(_probe_row(timestamp_ms="10000"))
    # Feed many AP rows at a much later timestamp — they should not age
    # out the probe_req device because the clock must not advance.
    for i in range(20):
        tracker.update(_ap_row(rssi="-90", timestamp_ms=str(50000 + i)))
    assert len(tracker.current_devices()) == 1


# ---------------------------------------------------------------------------
# Sorting + multiple devices
# ---------------------------------------------------------------------------

def test_sorted_by_rssi_descending() -> None:
    """current_devices returns strongest signal first."""
    tracker = DeviceTracker()
    tracker.update(_probe_row(src_mac="weak",    rssi="-85", timestamp_ms="10000"))
    tracker.update(_probe_row(src_mac="strong",  rssi="-40", timestamp_ms="10000"))
    tracker.update(_probe_row(src_mac="medium",  rssi="-65", timestamp_ms="10000"))
    devices = tracker.current_devices()
    assert [d.mac for d in devices] == ["strong", "medium", "weak"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_mac_is_ignored() -> None:
    """A probe_req row with an empty src_mac is skipped."""
    tracker = DeviceTracker()
    tracker.update(_probe_row(src_mac="", timestamp_ms="10000"))
    assert tracker.current_devices() == []


def test_malformed_rssi_is_ignored() -> None:
    """A row with a non-integer RSSI is skipped without raising."""
    tracker = DeviceTracker()
    tracker.update(_probe_row(rssi="not-a-number", timestamp_ms="10000"))
    assert tracker.current_devices() == []


def test_missing_timestamp_is_ignored() -> None:
    """A row missing the timestamp_ms key is skipped without raising."""
    tracker = DeviceTracker()
    row = _probe_row()
    del row["timestamp_ms"]
    tracker.update(row)
    assert tracker.current_devices() == []


def test_clear_resets_state() -> None:
    """clear() removes all devices and resets the clock."""
    tracker = DeviceTracker()
    tracker.update(_probe_row(timestamp_ms="10000"))
    tracker.update(_probe_row(src_mac="11:22:33:44:55:66",
                              timestamp_ms="10500"))
    assert len(tracker.current_devices()) == 2
    tracker.clear()
    assert tracker.current_devices() == []


def test_device_info_to_dict() -> None:
    """DeviceInfo.to_dict returns a JSON-serialisable dict with all fields."""
    tracker = DeviceTracker()
    tracker.update(_probe_row(rssi="-55", timestamp_ms="10000",
                              ssid="HomeNet", channel="6",
                              est_distance_m="2.34"))
    devices = tracker.current_devices()
    d = devices[0].to_dict()
    assert d == {
        "mac": "aa:bb:cc:dd:ee:ff",
        "rssi": -55,
        "last_seen": 10000,
        "ssid": "HomeNet",
        "channel": 6,
        "est_distance_m": 2.34,
    }


def test_empty_tracker_returns_empty_list() -> None:
    """A fresh tracker with no updates returns an empty device list."""
    tracker = DeviceTracker()
    assert tracker.current_devices() == []
