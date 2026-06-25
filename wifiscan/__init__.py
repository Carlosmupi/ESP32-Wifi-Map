"""wifiscan — host-side tooling for the Wi-Fi Scanner with Signal Map firmware.

The firmware emits CSV over USB serial describing Wi-Fi scans per spot.
This package holds the single source of truth for that CSV schema so the
capturing tool (``capture.py``) and the visualization tool (``heatmap.py``)
agree on column names, header format, footer format, and filename
sanitization without copy-pasting any of it.

See :mod:`wifiscan.schema` for the canonical column list, header string,
row/footer parsers, and ``safe_fieldname``.
"""

__all__ = ["schema", "merge", "device_tracker", "live"]
