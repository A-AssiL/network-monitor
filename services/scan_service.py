"""Scan service.
​
Coordinates network discovery: it runs an ARP scan on a background thread
(reusing :class:`~app.network.scanner.ArpScanner`), **persists** the results to
the SQLite database, and re-emits Qt signals so any number of views can react
without knowing about threads or the database.
​
Why a service (vs. the Devices page's own worker)?
--------------------------------------------------
- Keeps persistence in one place: the Devices page stays a pure view, while the
  service owns the scan lifecycle *and* the database write-through.
- Lets the Dashboard (device counts) and History page consume the same scan
  results the Devices page shows.
​
Threading & safety
------------------
- The blocking scan runs inside a private :class:`QThread`; the GUI thread
  never performs network or database I/O.
- Persistence happens on the worker thread *before* ``finished_scan`` is
  emitted. The database layer is thread-safe (its own lock), so this is safe
  and keeps the UI responsive.
- Carries the lifetime fix proven in the Devices page: an ``isValid()`` guard
  plus nulling the reference before ``deleteLater()`` avoids the
  ``libshiboken: Internal C++ object already deleted`` crash on repeat scans.
- Database writes are **best-effort and API-tolerant**: results are always
  returned/emitted even if persistence is unavailable or a method is missing.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal

logger = logging.getLogger(__name__)


# shiboken6.isValid guards against touching a Qt object whose underlying C++
# instance has already been deleted. Soft-imported so a missing shiboken6 never
# stops this service from loading (it is soft-imported by the main window).
try:  # pragma: no cover - depends on the runtime environment
    from shiboken6 import isValid as _shiboken_is_valid

    def qt_is_valid(obj: object) -> bool:
        try:
            return bool(_shiboken_is_valid(obj))
        except Exception:
            return True
except Exception:  # pragma: no cover
    def qt_is_valid(obj: object) -> bool:
        return True


class _ScanWorker(QThread):
    """Runs one ARP scan (and persists it) on a background thread."""

    device_found = Signal(object)
    finished_scan = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        database: object | None = None,
        subnet: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._database = database
        self._subnet = subnet
        self._scanner = None

    def run(self) -> None:  # noqa: D401 - QThread entry point
        """Perform the scan, persist results, and emit them."""
        try:
            from app.network.scanner import ArpScanner, ScannerError
        except Exception as exc:  # scapy / import failure
            self.error.emit(f"Scanner unavailable: {exc}")
            return

        try:
            self._scanner = ArpScanner()
            devices = self._scanner.scan(
                subnet=self._subnet,
                on_device=self.device_found.emit,
            )
            self._persist(devices)
            self.finished_scan.emit(devices)
        except ScannerError as exc:
            logger.warning("Scan failed: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:  # defensive: never crash the thread silently
            logger.exception("Unexpected scan error")
            self.error.emit(f"Unexpected error during scan: {exc}")

    def cancel(self) -> None:
        """Request cancellation of the running scan."""
        if self._scanner is not None:
            self._scanner.stop()

    # -- persistence (best-effort, API-tolerant) -------------------------
    def _persist(self, devices: list) -> None:
        """Write scan results to the database, tolerating its exact API."""
        db = self._database
        if db is None:
            return
        try:
            # Mark everything offline first so devices no longer seen flip to
            # offline, then upsert what the scan found (which marks them online).
            self._call_optional(db, "set_all_offline")
            upsert = getattr(db, "upsert_device", None)
            if callable(upsert):
                for device in devices:
                    try:
                        upsert(device)
                    except Exception as exc:  # one bad row must not abort the rest
                        logger.debug("upsert_device failed: %s", exc)
            self._record_event(db, devices)
        except Exception:  # persistence must never break the scan result
            logger.exception("Persisting scan results failed")

    @staticmethod
    def _call_optional(db: object, name: str) -> None:
        """Call a no-argument db method if it exists."""
        method = getattr(db, name, None)
        if callable(method):
            try:
                method()
            except Exception as exc:
                logger.debug("%s() failed: %s", name, exc)

    def _record_event(self, db: object, devices: list) -> None:
        """Record a discovery/scan event, trying safe keyword signatures.

        Keyword arguments are used so a mismatched parameter name raises
        ``TypeError`` (and is skipped) rather than silently storing the wrong
        value. If none match, the event is simply not recorded.
        """
        count = len(devices)
        attempts = (
            {"devices": devices},
            {"devices": devices, "subnet": self._subnet},
            {"device_count": count, "subnet": self._subnet},
            {"device_count": count},
            {"count": count},
        )
        for name in ("record_scan", "record_discovery"):
            method = getattr(db, name, None)
            if not callable(method):
                continue
            for kwargs in attempts:
                try:
                    method(**kwargs)
                    return
                except TypeError:
                    continue  # signature mismatch -> try the next shape
                except Exception as exc:
                    logger.debug("%s(%s) failed: %s", name, kwargs, exc)
                    return
        logger.debug("No compatible scan-event recorder found on database")


class ScanService(QObject):
    """Runs ARP scans on a background thread and persists the results.

    Signals
    -------
    scan_started():
        Emitted right before a scan begins.
    device_found(object):
        Emitted for each device as it is discovered.
    finished_scan(list):
        Emitted with the full device list when a scan completes.
    error(str):
        Emitted with a human-readable message if the scan fails.
    """

    scan_started = Signal()
    device_found = Signal(object)
    finished_scan = Signal(list)
    error = Signal(str)

    def __init__(self, database: object | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._database = database
        self._worker: _ScanWorker | None = None

    # -- state -----------------------------------------------------------
    @property
    def is_scanning(self) -> bool:
        """Whether a scan is currently running."""
        worker = self._worker
        return worker is not None and qt_is_valid(worker) and worker.isRunning()

    # -- control ---------------------------------------------------------
    def start_scan(self, subnet: str | None = None) -> None:
        """Kick off a background scan, guarding against concurrent runs."""
        # A finished QThread may have had its C++ object deleted while the
        # Python reference lingers; qt_is_valid() guards against touching it.
        if (
            self._worker is not None
            and qt_is_valid(self._worker)
            and self._worker.isRunning()
        ):
            logger.debug("Scan already in progress; ignoring request")
            return

        self._worker = None  # clear any finished/stale worker

        worker = _ScanWorker(database=self._database, subnet=subnet)
        worker.device_found.connect(self.device_found)
        worker.finished_scan.connect(self.finished_scan)
        worker.error.connect(self.error)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self.scan_started.emit()
        worker.start()

    def cancel(self) -> None:
        """Cancel the running scan, if any."""
        worker = self._worker
        if worker is not None and qt_is_valid(worker) and worker.isRunning():
            worker.cancel()

    def stop(self) -> None:
        """Cancel and wait for the worker to finish (for clean shutdown)."""
        worker = self._worker
        if worker is not None and qt_is_valid(worker) and worker.isRunning():
            worker.cancel()
            worker.wait(3000)

    # -- internals -------------------------------------------------------
    def _on_worker_finished(self) -> None:
        """Drop our reference before deleting the C++ object (crash-safe)."""
        worker = self._worker
        self._worker = None
        if worker is not None and qt_is_valid(worker):
            worker.deleteLater()


__all__ = ["ScanService"]
