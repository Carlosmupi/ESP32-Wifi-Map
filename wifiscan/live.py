"""wifiscan.live — real-time radar dashboard served from the host (issue #27).

When ``capture.py`` is run with ``--live``, this module starts a lightweight
HTTP server (stdlib only, no extra dependencies) that:

* Serves a single self-contained HTML page at ``/`` containing a radar
  canvas + device list (adapted from the Wifi-Radar-Scanner-for-ESP32
  reference project's UI, but driven by Server-Sent Events instead of
  WebSocket).
* Streams parsed rows to every connected browser via an SSE endpoint at
  ``/events``.  Two event types are pushed per row:
  - ``row``: the raw parsed row dict (so AP rows are visible too).
  - ``devices``: a snapshot from :class:`DeviceTracker` (issue #28) so
  the radar shows deduplicated, aged devices rather than a per-frame
  firehose.

The CSV-writing path in ``capture.py`` is unchanged whether or not
``--live`` is set; live mode is purely additive.
"""

from __future__ import annotations

import http.server
import json
import queue
import threading
import time
from dataclasses import asdict
from typing import Optional

from wifiscan.device_tracker import DeviceTracker, DeviceInfo

__all__ = ["LiveServer", "build_sse_message", "HTML_PAGE"]

DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8080


# ---------------------------------------------------------------------------
# SSE message builder (extracted for testability — no server needed)
# ---------------------------------------------------------------------------

def build_sse_message(row: dict, devices: list[DeviceInfo]) -> str:
    """Build the SSE text payload for one broadcast cycle.

    Returns a string containing two SSE events (``row`` and ``devices``)
    separated by blank lines, ready to be encoded and written to every
    connected client's stream.
    """
    row_json = json.dumps(row, default=str)
    devices_json = json.dumps([d.to_dict() for d in devices], default=str)
    return (
        f"event: row\ndata: {row_json}\n\n"
        f"event: devices\ndata: {devices_json}\n\n"
    )


# ---------------------------------------------------------------------------
# HTML page (self-contained, no external assets)
# ---------------------------------------------------------------------------

HTML_PAGE: str = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32 Wi-Fi Radar</title>
<style>
  :root {
    --phosphor: #00ff41;
    --phosphor-dim: rgba(0,255,65,0.15);
    --amber: #ffb000;
    --amber-dim: rgba(255,176,0,0.3);
    --bg: #0a0e0a;
    --panel-bg: rgba(0,20,0,0.6);
    --text-dim: rgba(0,255,65,0.5);
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--phosphor);
    font-family: 'Consolas', 'Courier New', monospace;
    margin: 0; padding: 16px; height: 100vh; overflow: hidden;
    text-shadow: 0 0 4px rgba(0,255,65,0.4);
  }
  .container { max-width: 1200px; margin: 0 auto; height: 100%;
    display: flex; flex-direction: column; }
  header { text-align: center; padding: 8px 0; }
  h1 { font-size: 1.8rem; margin: 0; letter-spacing: 4px; text-transform: uppercase;
    color: var(--phosphor); text-shadow: 0 0 8px rgba(0,255,65,0.6); }
  .status-line { font-size: .8rem; letter-spacing: 1px; margin: 4px 0 0; }
  .dashboard { display: flex; flex: 1; gap: 16px; min-height: 0; }
  .radar-container { flex: 3; background: rgba(0,8,0,0.5);
    border: 1px solid var(--phosphor-dim); border-radius: 4px;
    overflow: hidden; position: relative;
    box-shadow: 0 0 20px rgba(0,255,65,0.05) inset; }
  .radar-container::after {
    content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    background: repeating-linear-gradient(0deg,
      rgba(0,0,0,0) 0px, rgba(0,0,0,0) 2px,
      rgba(0,0,0,0.15) 3px, rgba(0,0,0,0.15) 3px);
    pointer-events: none; z-index: 1;
  }
  #radar { width: 100%; height: 100%; display: block; }
  .side-panel { flex: 1; display: flex; flex-direction: column; gap: 12px;
    min-width: 260px; }
  .panel { background: var(--panel-bg); border-radius: 4px; padding: 12px;
    border: 1px solid var(--phosphor-dim); overflow-y: auto;
    box-shadow: 0 0 12px rgba(0,255,65,0.03) inset; }
  .panel h2 { font-size: .85rem; margin: 0 0 10px; color: var(--phosphor);
    letter-spacing: 2px; text-transform: uppercase;
    border-bottom: 1px solid var(--phosphor-dim); padding-bottom: 6px; }
  .device-card, .ap-card {
    background: rgba(0,30,0,0.4); border-radius: 2px; padding: 8px;
    margin-bottom: 6px; animation: fadeIn .3s;
  }
  .ap-card { border-left: 2px solid var(--amber); }
  .device-card { border-left: 2px solid var(--phosphor); }
  @keyframes fadeIn { from { opacity:0; transform: translateY(4px); } to { opacity:1; } }
  .card-title { font-size: .8rem; font-weight: bold; margin: 0 0 4px;
    word-break: break-all; color: var(--phosphor); }
  .ap-card .card-title { color: var(--amber); }
  .card-meta { font-size: .7rem; color: var(--text-dim); margin: 0; }
  .signal-bar { height: 3px; background: rgba(0,255,65,0.08);
    border-radius: 1px; margin-top: 5px; overflow: hidden; }
  .signal-level { height: 100%; border-radius: 1px; }
  .signal-level.probe { background: linear-gradient(90deg, rgba(0,255,65,0.3), var(--phosphor)); }
  .signal-level.ap { background: linear-gradient(90deg, rgba(255,176,0,0.3), var(--amber)); }
  footer { text-align: center; padding: 8px; font-size: .7rem;
    color: var(--text-dim); letter-spacing: 1px; }
  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    margin-right: 4px; }
  .status-dot.connected { background: var(--phosphor); box-shadow: 0 0 6px var(--phosphor); }
  .status-dot.disconnected { background: #f44; box-shadow: 0 0 6px #f44; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>ESP32 Wi-Fi Radar</h1>
    <p class="status-line"><span id="statusDot" class="status-dot disconnected"></span><span id="statusText">CONNECTING...</span></p>
  </header>
  <div class="dashboard">
    <div class="radar-container"><canvas id="radar"></canvas></div>
    <div class="side-panel">
      <div class="panel" style="flex:1"><h2>Devices / Probe-Req</h2><div id="deviceList"></div></div>
      <div class="panel" style="flex:1"><h2>Access Points</h2><div id="apList"></div></div>
    </div>
  </div>
  <footer>ESP32 WI-FI SIGNAL MAP // LIVE SSE DASHBOARD</footer>
</div>
<script>
const radar = document.getElementById('radar');
const ctx = radar.getContext('2d');
const deviceListEl = document.getElementById('deviceList');
const apListEl = document.getElementById('apList');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');

function resizeCanvas() {
  radar.width = radar.offsetWidth;
  radar.height = radar.offsetHeight;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

function hashMacToAngle(mac) {
  let sum = 0;
  for (const part of mac.split(':')) { sum += parseInt(part, 16) || 0; }
  return sum % 360;
}
function rssiToStrength(rssi) {
  return Math.max(0, Math.min(100, Math.round((rssi + 95) / 60 * 100)));
}

// --- Mouse tracking for hover tooltip ---
let mousePos = null;
let radarDots = []; // reset each frame: {x, y, type, data}

radar.addEventListener('mousemove', (e) => {
  const rect = radar.getBoundingClientRect();
  mousePos = { x: e.clientX - rect.left, y: e.clientY - rect.top };
});
radar.addEventListener('mouseleave', () => { mousePos = null; });

function drawRadar() {
  const w = radar.width, h = radar.height;
  const cx = w / 2, cy = h / 2;
  const radius = Math.min(cx, cy) * 0.88;
  ctx.clearRect(0, 0, w, h);

  // concentric range rings
  ctx.strokeStyle = 'rgba(0,255,65,0.12)';
  ctx.lineWidth = 1;
  for (let i = 1; i <= 5; i++) {
    ctx.beginPath(); ctx.arc(cx, cy, radius * i / 5, 0, Math.PI * 2); ctx.stroke();
  }
  // distance labels (2m, 4m, 6m, 8m, 10m) along horizontal axis
  ctx.font = '10px Consolas, monospace';
  ctx.textAlign = 'center';
  ctx.fillStyle = 'rgba(0,255,65,0.35)';
  for (let i = 1; i <= 5; i++) {
    const ringR = radius * i / 5;
    const label = (i * 2) + 'm';
    ctx.fillText(label, cx + ringR, cy - 4);
  }
  // crosshairs
  ctx.strokeStyle = 'rgba(0,255,65,0.2)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx, 0); ctx.lineTo(cx, h);
  ctx.moveTo(0, cy); ctx.lineTo(w, cy);
  ctx.stroke();
  // tick marks on crosshairs
  for (let i = 1; i <= 5; i++) {
    const tickR = radius * i / 5;
    ctx.beginPath(); ctx.moveTo(cx + tickR, cy - 3); ctx.lineTo(cx + tickR, cy + 3);
    ctx.moveTo(cx - tickR, cy - 3); ctx.lineTo(cx - tickR, cy + 3);
    ctx.moveTo(cx - 3, cy + tickR); ctx.lineTo(cx + 3, cy + tickR);
    ctx.moveTo(cx - 3, cy - tickR); ctx.lineTo(cx + 3, cy - tickR);
    ctx.stroke();
  }

  // sweep line
  const sweepAngle = (Date.now() / 30) % 360;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + radius * Math.cos((sweepAngle - 90) * Math.PI / 180),
             cy + radius * Math.sin((sweepAngle - 90) * Math.PI / 180));
  ctx.strokeStyle = 'rgba(0,255,65,0.5)';
  ctx.lineWidth = 2;
  ctx.stroke();
}

let currentDevices = [];
let currentAps = [];

function drawDeviceDots() {
  const w = radar.width, h = radar.height;
  const cx = w / 2, cy = h / 2;
  const radius = Math.min(cx, cy) * 0.88;
  for (const dev of currentDevices) {
    const angle = hashMacToAngle(dev.mac) * Math.PI / 180;
    const dist = radius * Math.min(dev.est_distance_m / 10, 1);
    const x = cx + dist * Math.cos(angle - Math.PI / 2);
    const y = cy + dist * Math.sin(angle - Math.PI / 2);
    const strength = rssiToStrength(dev.rssi);
    // dot
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(0,255,65,${strength / 100})`;
    ctx.fill();
    // pulse
    const pulseR = 12 * (1 + Math.sin(Date.now() / 250 + dev.mac.charCodeAt(0)) / 2);
    ctx.beginPath(); ctx.arc(x, y, pulseR, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(0,255,65,0.2)'; ctx.lineWidth = 1.5; ctx.stroke();
    // register for hover
    radarDots.push({ x, y, type: 'device', data: dev });
  }
}

function drawApDots() {
  const w = radar.width, h = radar.height;
  const cx = w / 2, cy = h / 2;
  const radius = Math.min(cx, cy) * 0.88;
  for (const ap of currentAps) {
    const angle = hashMacToAngle(ap.bssid || ap.ssid) * Math.PI / 180;
    const dist = radius * Math.min(Number(ap.est_distance_m) / 10, 1);
    const x = cx + dist * Math.cos(angle - Math.PI / 2);
    const y = cy + dist * Math.sin(angle - Math.PI / 2);
    const strength = rssiToStrength(Number(ap.rssi));
    // dot
    ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(255,176,0,${0.4 + strength / 200})`;
    ctx.fill();
    // ring
    ctx.beginPath(); ctx.arc(x, y, 10, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(255,176,0,0.3)'; ctx.lineWidth = 1; ctx.stroke();
    // register for hover
    radarDots.push({ x, y, type: 'ap', data: ap });
  }
}

function drawHoverTooltip() {
  if (!mousePos) return;
  // find nearest dot within 20px
  let nearest = null, nearestDist = 20;
  for (const dot of radarDots) {
    const dx = dot.x - mousePos.x, dy = dot.y - mousePos.y;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < nearestDist) { nearestDist = d; nearest = dot; }
  }
  if (!nearest) return;

  const w = radar.width, h = radar.height;
  const cx = w / 2, cy = h / 2;
  const radius = Math.min(cx, cy) * 0.88;

  // highlight hovered dot
  ctx.beginPath(); ctx.arc(nearest.x, nearest.y, 14, 0, Math.PI * 2);
  ctx.strokeStyle = nearest.type === 'ap' ? 'rgba(255,176,0,0.8)' : 'rgba(0,255,65,0.8)';
  ctx.lineWidth = 2; ctx.stroke();

  // build tooltip lines
  let lines, color;
  if (nearest.type === 'ap') {
    const ap = nearest.data;
    color = '#ffb000';
    lines = [
      ap.ssid || '(hidden)',
      'BSSID: ' + (ap.bssid || '?'),
      'CH: ' + (ap.channel || '?') + '  RSSI: ' + ap.rssi + ' dBm',
      'DIST: ' + Number(ap.est_distance_m).toFixed(1) + ' m',
      'AUTH: ' + (ap.auth_mode || '?'),
    ];
  } else {
    const dev = nearest.data;
    color = '#00ff41';
    lines = [
      dev.ssid || dev.mac,
      'MAC: ' + dev.mac,
      'RSSI: ' + dev.rssi + ' dBm',
      'DIST: ' + dev.est_distance_m.toFixed(1) + ' m',
      'CH: ' + (dev.channel || '?'),
    ];
  }

  // measure tooltip
  ctx.font = '11px Consolas, monospace';
  let maxW = 0;
  for (const ln of lines) { maxW = Math.max(maxW, ctx.measureText(ln).width); }
  const padX = 8, padY = 6, lineH = 15;
  const boxW = maxW + padX * 2;
  const boxH = lines.length * lineH + padY * 2;

  // position: offset right+down from dot, flip if near edge
  let bx = nearest.x + 16, by = nearest.y + 16;
  if (bx + boxW > w) bx = nearest.x - boxW - 16;
  if (by + boxH > h) by = nearest.y - boxH - 16;

  // tooltip box
  ctx.fillStyle = 'rgba(0,12,0,0.92)';
  ctx.fillRect(bx, by, boxW, boxH);
  ctx.strokeStyle = color; ctx.lineWidth = 1;
  ctx.strokeRect(bx, by, boxW, boxH);

  // tooltip text
  ctx.textAlign = 'left';
  for (let i = 0; i < lines.length; i++) {
    ctx.fillStyle = i === 0 ? color : 'rgba(0,255,65,0.7)';
    ctx.fillText(lines[i], bx + padX, by + padY + (i + 1) * lineH - 4);
  }
}

function updateDeviceCards(devices) {
  if (devices.length === 0) {
    deviceListEl.innerHTML = '<p class="card-meta">NO DEVICES DETECTED</p>';
    return;
  }
  deviceListEl.innerHTML = devices.map(d => {
    const s = rssiToStrength(d.rssi);
    return `<div class="device-card">
      <p class="card-title">${d.mac}</p>
      <p class="card-meta">${d.ssid || '(wildcard)'} | ${d.est_distance_m.toFixed(1)} m | ${d.rssi} dBm</p>
      <div class="signal-bar"><div class="signal-level probe" style="width:${s}%"></div></div>
    </div>`;
  }).join('');
}

const apMap = new Map(); // bssid -> row
function updateApCard(row) {
  apMap.set(row.bssid || row.ssid, row);
  const aps = [...apMap.values()].sort((a, b) => Number(b.rssi) - Number(a.rssi));
  apListEl.innerHTML = aps.map(r => {
    const s = rssiToStrength(Number(r.rssi));
    return `<div class="ap-card">
      <p class="card-title">${r.ssid || '(hidden)'}</p>
      <p class="card-meta">${r.bssid} | ch ${r.channel} | ${r.rssi} dBm | ${r.auth_mode}</p>
      <div class="signal-bar"><div class="signal-level ap" style="width:${s}%"></div></div>
    </div>`;
  }).join('');
}

function animate() {
  radarDots = [];
  drawRadar();
  drawApDots();
  drawDeviceDots();
  drawHoverTooltip();
  requestAnimationFrame(animate);
}
animate();

// --- SSE connection ---
const es = new EventSource('/events');
es.addEventListener('open', () => {
  statusDot.className = 'status-dot connected';
  statusText.textContent = 'LIVE';
});
es.addEventListener('error', () => {
  statusDot.className = 'status-dot disconnected';
  statusText.textContent = 'RECONNECTING...';
});
es.addEventListener('row', (e) => {
  const row = JSON.parse(e.data);
  if (row.frame_type === 'ap') {
    updateApCard(row);
    currentAps = [...apMap.values()];
  }
});
es.addEventListener('devices', (e) => {
  currentDevices = JSON.parse(e.data);
  updateDeviceCards(currentDevices);
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Live server
# ---------------------------------------------------------------------------

class _SSEClient:
    """Per-client SSE state: a write queue + the wfile to write to."""

    def __init__(self, wfile) -> None:
        self.wfile = wfile
        self.queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=256)


class LiveServer:
    """HTTP + SSE server for the live radar dashboard.

    Usage::

        server = LiveServer(port=8080)
        server.start()
        for row in serial_stream:
            server.broadcast(row)
        server.stop()
    """

    def __init__(self, host: str = DEFAULT_WEB_HOST,
                 port: int = DEFAULT_WEB_PORT) -> None:
        self._tracker = DeviceTracker()
        self._clients: list[_SSEClient] = []
        self._lock = threading.Lock()

        live_server = self  # captured by the handler closure

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (stdlib naming)
                if self.path == "/":
                    self._serve_html()
                elif self.path == "/events":
                    self._serve_sse()
                else:
                    self.send_error(404)

            def _serve_html(self) -> None:
                body = HTML_PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _serve_sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                client = _SSEClient(self.wfile)
                with live_server._lock:
                    live_server._clients.append(client)
                try:
                    while True:
                        try:
                            data = client.queue.get(timeout=30)
                        except queue.Empty:
                            data = b": ping\n\n"  # keep-alive comment
                        if data is None:
                            break  # shutdown sentinel
                        self.wfile.write(data)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with live_server._lock:
                        if client in live_server._clients:
                            live_server._clients.remove(client)

            def log_message(self, *args) -> None:
                pass  # suppress stderr request logging

        self._httpd = http.server.ThreadingHTTPServer((host, port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True
        )
        self._host = host
        self._port = port

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def start(self) -> None:
        """Start the HTTP server in a background daemon thread."""
        self._thread.start()

    def broadcast(self, row: dict) -> None:
        """Feed a parsed row to the device tracker and push SSE events.

        Called from the capture serial loop for every successfully parsed
        row.  The row is fed to :class:`DeviceTracker`, then both the raw
        row and the updated device snapshot are pushed to every connected
        SSE client.  Dead clients (broken pipe, full queue) are reaped.
        """
        self._tracker.update(row)
        devices = self._tracker.current_devices()
        msg = build_sse_message(row, devices).encode("utf-8")

        with self._lock:
            dead: list[_SSEClient] = []
            for client in self._clients:
                try:
                    client.queue.put_nowait(msg)
                except queue.Full:
                    dead.append(client)
            for client in dead:
                self._clients.remove(client)

    def stop(self) -> None:
        """Shut down the HTTP server and signal SSE clients to close."""
        with self._lock:
            for client in self._clients:
                try:
                    client.queue.put_nowait(None)
                except queue.Full:
                    pass
            self._clients.clear()
        # shutdown() blocks until serve_forever() exits, so only call it
        # if the server thread is actually running (was started and is
        # still alive). Otherwise just close the listening socket.
        if self._thread.is_alive():
            self._httpd.shutdown()
        self._httpd.server_close()
