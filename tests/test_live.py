"""pytest suite for wifiscan.live (issue #27).

Tests the SSE message builder (no real server, no real serial) and the
LiveServer end-to-end with a real localhost socket — verifying that a
parsed row is serialized to the expected JSON shape and pushed to a
connected SSE client.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from wifiscan.live import build_sse_message, LiveServer, HTML_PAGE  # noqa: E402
from wifiscan.device_tracker import DeviceInfo  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe_row(*, src_mac="aa:bb:cc:dd:ee:ff", rssi="-55",
               timestamp_ms="10000", ssid="HomeNet",
               est_distance_m="2.34") -> dict:
    return {
        "spot_id": "1", "spot_label": "lab",
        "timestamp_ms": timestamp_ms, "ssid": ssid,
        "bssid": "", "rssi": rssi, "channel": "6",
        "auth_mode": "", "est_distance_m": est_distance_m,
        "frame_type": "probe_req", "src_mac": src_mac,
    }


def _ap_row(*, ssid="MyAP", bssid="11:22:33:44:55:66",
            rssi="-60") -> dict:
    return {
        "spot_id": "1", "spot_label": "lab",
        "timestamp_ms": "10000", "ssid": ssid, "bssid": bssid,
        "rssi": rssi, "channel": "11", "auth_mode": "WPA2_PSK",
        "est_distance_m": "3.5", "frame_type": "ap", "src_mac": "",
    }


# ---------------------------------------------------------------------------
# build_sse_message — pure function, no server
# ---------------------------------------------------------------------------

def test_sse_message_contains_row_event() -> None:
    """The SSE payload includes an event: row line with the row as JSON."""
    row = _ap_row()
    msg = build_sse_message(row, devices=[])
    assert "event: row" in msg
    # Extract the JSON from the data: line of the row event.
    row_section = msg.split("event: row\ndata: ")[1].split("\n\n")[0]
    parsed = json.loads(row_section)
    assert parsed["ssid"] == "MyAP"
    assert parsed["frame_type"] == "ap"


def test_sse_message_contains_devices_event() -> None:
    """The SSE payload includes an event: devices line with device dicts."""
    devices = [
        DeviceInfo(mac="aa:bb:cc:dd:ee:ff", rssi=-55, last_seen=10000,
                   ssid="HomeNet", channel=6, est_distance_m=2.34),
    ]
    msg = build_sse_message(_probe_row(), devices)
    assert "event: devices" in msg
    dev_section = msg.split("event: devices\ndata: ")[1].split("\n\n")[0]
    parsed = json.loads(dev_section)
    assert len(parsed) == 1
    assert parsed[0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert parsed[0]["rssi"] == -55


def test_sse_message_with_no_devices() -> None:
    """An empty device list produces a valid empty JSON array."""
    msg = build_sse_message(_ap_row(), devices=[])
    dev_section = msg.split("event: devices\ndata: ")[1].split("\n\n")[0]
    assert json.loads(dev_section) == []


def test_sse_message_row_preserves_all_columns() -> None:
    """Every EXPECTED_COLUMNS key is present in the serialized row."""
    from wifiscan.schema import EXPECTED_COLUMNS
    row = _probe_row()
    msg = build_sse_message(row, devices=[])
    row_section = msg.split("event: row\ndata: ")[1].split("\n\n")[0]
    parsed = json.loads(row_section)
    for col in EXPECTED_COLUMNS:
        assert col in parsed


# ---------------------------------------------------------------------------
# HTML page sanity
# ---------------------------------------------------------------------------

def test_html_page_is_self_contained() -> None:
    """The served HTML has no external src/href dependencies."""
    assert "<script" in HTML_PAGE
    assert "EventSource" in HTML_PAGE
    # No external script or stylesheet references.
    assert 'src="http' not in HTML_PAGE
    assert 'href="http' not in HTML_PAGE


# ---------------------------------------------------------------------------
# LiveServer — HTML serving (real localhost socket)
# ---------------------------------------------------------------------------

def test_live_server_serves_html_page() -> None:
    """GET / returns the HTML page with correct content type."""
    server = LiveServer(port=0)  # ephemeral port
    server.start()
    try:
        url = f"http://127.0.0.1:{server._httpd.server_address[1]}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            assert "ESP32 Wi-Fi Radar" in body
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# LiveServer — broadcast via direct queue inspection (no HTTP streaming)
# ---------------------------------------------------------------------------

def test_broadcast_pushes_row_and_devices_to_client_queue() -> None:
    """broadcast() enqueues both a row event and a devices event."""
    from wifiscan.live import _SSEClient
    import io

    server = LiveServer(port=0)
    # Manually attach a mock client (no need to start the HTTP server).
    mock_client = _SSEClient(io.BytesIO())
    server._clients.append(mock_client)

    server.broadcast(_probe_row(rssi="-55", src_mac="aa:bb:cc:dd:ee:ff"))

    data = mock_client.queue.get_nowait().decode("utf-8")
    # The message contains both events.
    assert "event: row" in data
    assert "event: devices" in data
    # Row event has the probe_req frame_type.
    row_section = data.split("event: row\ndata: ")[1].split("\n\n")[0]
    parsed_row = json.loads(row_section)
    assert parsed_row["frame_type"] == "probe_req"
    assert parsed_row["src_mac"] == "aa:bb:cc:dd:ee:ff"
    # Devices event has one tracked device.
    dev_section = data.split("event: devices\ndata: ")[1].split("\n\n")[0]
    parsed_devs = json.loads(dev_section)
    assert len(parsed_devs) == 1
    assert parsed_devs[0]["mac"] == "aa:bb:cc:dd:ee:ff"


def test_broadcast_ap_row_produces_empty_devices() -> None:
    """An AP row is pushed as a row event; the devices snapshot is empty."""
    from wifiscan.live import _SSEClient
    import io

    server = LiveServer(port=0)
    mock_client = _SSEClient(io.BytesIO())
    server._clients.append(mock_client)

    server.broadcast(_ap_row())

    data = mock_client.queue.get_nowait().decode("utf-8")
    row_section = data.split("event: row\ndata: ")[1].split("\n\n")[0]
    parsed_row = json.loads(row_section)
    assert parsed_row["frame_type"] == "ap"
    dev_section = data.split("event: devices\ndata: ")[1].split("\n\n")[0]
    assert json.loads(dev_section) == []


def test_broadcast_dedupes_multiple_probe_req_rows() -> None:
    """Repeated broadcasts from the same MAC produce one device in snapshot."""
    from wifiscan.live import _SSEClient
    import io

    server = LiveServer(port=0)
    mock_client = _SSEClient(io.BytesIO())
    server._clients.append(mock_client)

    for i in range(5):
        server.broadcast(_probe_row(rssi="-55", timestamp_ms=str(10000 + i * 100)))

    # The last enqueued message's devices snapshot should have exactly 1 device.
    last_data = None
    while not mock_client.queue.empty():
        last_data = mock_client.queue.get_nowait().decode("utf-8")
    assert last_data is not None
    dev_section = last_data.split("event: devices\ndata: ")[1].split("\n\n")[0]
    assert len(json.loads(dev_section)) == 1


def test_broadcast_reaps_dead_client_on_full_queue() -> None:
    """A client whose queue is full is silently removed (no crash)."""
    from wifiscan.live import _SSEClient
    import io

    server = LiveServer(port=0)
    mock_client = _SSEClient(io.BytesIO())
    # Fill the queue to capacity.
    for _ in range(mock_client.queue.maxsize):
        mock_client.queue.put_nowait(b"x")
    server._clients.append(mock_client)

    # This broadcast should not raise; the full client is reaped.
    server.broadcast(_probe_row())
    assert mock_client not in server._clients


def test_live_server_url_property() -> None:
    """The url property returns a properly formatted http URL."""
    server = LiveServer(host="127.0.0.1", port=9999)
    assert server.url == "http://127.0.0.1:9999"
    server.stop()  # stop the unstarted server cleanly
