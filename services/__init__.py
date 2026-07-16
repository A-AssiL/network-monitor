"""Application services layer.
​
Coordinates the domain logic (:mod:`app.network`) and persistence
(:mod:`app.database`) on behalf of the UI, and hosts the cross-cutting
features designed to be added later without refactoring the core:
​
- background scan / bandwidth-monitor / capture workers (QThread orchestration)
- batched persistence write-through and history read-back
- unknown-device alerting and notifications (future)
- CSV / PDF export, speed test, SNMP / router-API integration (future)
​
Design
------
- Services may depend on :mod:`app.network` and :mod:`app.database`, but must
  **not** import from :mod:`app.ui`. They communicate results outward via
  callbacks or Qt signals so the UI stays decoupled from the implementation.
- Keeping this seam in place is what lets the "future architecture" features
  drop in as new service modules rather than edits to existing pages.
​
Tolerant re-exports
-------------------
Each service is imported defensively. If a service module cannot be imported
(for example, an optional dependency such as Scapy is missing and pulls down
``capture_service``), the failure is logged and that name is simply omitted
from this package rather than crashing the whole app. Callers should therefore
feature-detect, e.g.::
​
    from app import services
    if services.CaptureService is not None:
        ...
​
Name-level access (``services.CaptureService``) always works and yields
``None`` when unavailable; ``from app.services import CaptureService`` works
whenever the module imported successfully.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Names we attempt to expose, mapped to their defining module.
_OPTIONAL_SERVICES: dict[str, str] = {
    "MonitorService": ".monitor_service",
    "ScanService": ".scan_service",
    "CaptureService": ".capture_service",
    "PersistenceService": ".persistence_service",
}

# Populated with whatever imported cleanly; unavailable names resolve to None.
MonitorService: Any = None
ScanService: Any = None
CaptureService: Any = None
PersistenceService: Any = None

__all__: list[str] = []


def _load_services() -> None:
    """Import each service defensively, recording the ones that succeed."""
    from importlib import import_module

    for name, module_path in _OPTIONAL_SERVICES.items():
        try:
            module = import_module(module_path, __name__)
            service = getattr(module, name)
        except Exception as exc:  # missing dep or import error -> skip this one
            logger.warning("Service %s unavailable: %s", name, exc)
            continue
        globals()[name] = service
        __all__.append(name)


_load_services()
