"""Background service layer for Network Monitor Pro.
​
Services are the bridge between the GUI (main thread) and the blocking network
and database work, which must never run on the GUI thread. Each service is a
``QObject``/``QThread`` that does its work off the main thread and communicates
results back via Qt signals:
​
- :class:`MonitorService`     -- emits periodic live bandwidth samples.
- :class:`ScanService`        -- runs ARP scans and persists discovered devices.
- :class:`PersistenceService` -- batches traffic samples to the database and
  reads history/devices back for the UI.
​
Imports are kept tolerant: a service whose optional dependency is missing (for
example PySide6 or Scapy) is simply exported as ``None`` instead of crashing
the whole package at import time. Callers should check for ``None`` (the main
window already does) before using a service.
"""

from __future__ import annotations

__all__ = [
    "MonitorService",
    "ScanService",
    "PersistenceService",
]

try:
    from services.monitor_service import MonitorService
except Exception:  # pragma: no cover - optional dependency may be missing
    MonitorService = None  # type: ignore[assignment]

try:
    from services.scan_service import ScanService
except Exception:  # pragma: no cover - optional dependency may be missing
    ScanService = None  # type: ignore[assignment]

try:
    from services.persistence_service import PersistenceService
except Exception:  # pragma: no cover - optional dependency may be missing
    PersistenceService = None  # type: ignore[assignment]
