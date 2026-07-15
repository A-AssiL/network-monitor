"""
Application services layer.
​
Coordinates the domain logic (:mod:`app.network`) and persistence
(:mod:`app.database`) on behalf of the UI, and hosts the cross-cutting
features designed to be added later without refactoring the core:
​
- background scan / bandwidth-monitor workers (QThread orchestration)
- unknown-device alerting and notifications
- CSV / PDF export
- speed test
- SNMP / router-API integration and remote monitoring
​
Design
------
- Services may depend on :mod:`app.network` and :mod:`app.database`, but must
  **not** import from :mod:`app.ui`. They communicate results outward via
  callbacks or Qt signals so the UI stays decoupled from the implementation.
- Keeping this seam in place from Phase 1 is what lets the "future
  architecture" features drop in as new service modules rather than edits to
  existing pages.
​
This package is currently a placeholder. Phase 1 keeps the scan worker inline
in :mod:`app.ui.devices_page`; it can move here unchanged (same signal
interface) as the first service.
"""

from __future__ import annotations

__all__: list[str] = []

# Phase 2+ convenience exports (uncomment as services are implemented):
# from .scan_service import ScanService
# from .monitor_service import MonitorService
# from .export_service import ExportService
