"""Persistence service.
​
Write-through + read-back layer between the live data streams and the SQLite
database, running all I/O on a background thread so the GUI never blocks.
​
Responsibilities
----------------
- **Buffer** bandwidth samples arriving (up to once per second) and flush them
  to the database in batches on a timer, instead of one transaction per
  sample.
- **Load** persisted traffic history and known devices back out, emitting the
  results as Qt signals for the History page, Dashboard, and Traffic charts.
​
Threading
---------
- A single :class:`QThread` owns a small command queue. It auto-flushes the
  sample buffer every ``flush_interval`` seconds and also handles explicit
  ``load`` requests.
- :meth:`record_sample` is safe to connect directly to the monitor's
  ``sample_ready`` signal; it only appends to a lock-protected buffer.
- Results are delivered via signals (``traffic_history_loaded`` /
  ``devices_loaded``), so callers stay off the I/O path.
​
API tolerance
-------------
Built to adapt to the concrete ``database.py``: it discovers bulk vs. per-row
write methods, tries object then keyword signatures, and reads history with
whatever ``get_traffic_history`` / ``get_devices`` signature is available.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque

from PySide6.QtCore import QObject, QThread, Signal, Slot

logger = logging.getLogger(__name__)

# Optional fields copied off a sample when falling back to keyword writes.
_OPTIONAL_SAMPLE_FIELDS = ("bytes_recv", "bytes_sent", "interface", "timestamp")

# Candidate bulk-write method names on the database.
_BULK_WRITE_NAMES = ("record_traffic_bulk", "record_traffic_many", "record_traffic_batch")


class PersistenceService(QObject):
    """
    Batched traffic write-through and history/device read-back.

    Parameters
    ----------
    database:
        The database object to persist to / read from. If ``None`` the service
        is inert (records are dropped, loads return empty), so the app still
        runs without persistence.
    flush_interval:
        Seconds between automatic buffer flushes.
    max_buffer:
        Buffer size that forces an immediate flush.
    """

    traffic_history_loaded = Signal(list)
    devices_loaded = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        database: object | None = None,
        flush_interval: float = 5.0,
        max_buffer: int = 1000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._database = database
        self._flush_interval = max(1.0, float(flush_interval))
        self._max_buffer = max(1, int(max_buffer))
        self._buffer: deque = deque()
        self._lock = threading.Lock()
        self._worker: _PersistenceWorker | None = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        """Start the background I/O thread."""
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _PersistenceWorker(self, self._flush_interval)
        self._worker.start()

    def stop(self) -> None:
        """Flush remaining samples and stop the background thread."""
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.shutdown()
            worker.wait(4000)

    # -- write path ------------------------------------------------------
    @Slot(object)
    def record_sample(self, sample: object) -> None:
        """Buffer a bandwidth sample for the next flush (thread-safe)."""
        if sample is None:
            return
        with self._lock:
            self._buffer.append(sample)
            overflow = len(self._buffer) >= self._max_buffer
        if overflow and self._worker is not None:
            self._worker.submit(("flush",))

    def flush(self) -> None:
        """Request an immediate flush of buffered samples."""
        if self._worker is not None:
            self._worker.submit(("flush",))

    # -- read path -------------------------------------------------------
    def load_traffic_history(self, limit: int = 600) -> None:
        """Asynchronously load traffic history; result via traffic_history_loaded."""
        if self._worker is not None:
            self._worker.submit(("load_traffic", limit))

    def load_devices(self) -> None:
        """Asynchronously load known devices; result via devices_loaded."""
        if self._worker is not None:
            self._worker.submit(("load_devices",))

    def load_all(self, traffic_limit: int = 600) -> None:
        """Convenience: load devices and traffic history together."""
        self.load_devices()
        self.load_traffic_history(traffic_limit)

    # -- internal buffer access (used by the worker) --------------------
    def _drain_buffer(self) -> list:
        """Atomically remove and return all buffered samples."""
        with self._lock:
            if not self._buffer:
                return []
            items = list(self._buffer)
            self._buffer.clear()
        return items


class _PersistenceWorker(QThread):
    """Background thread: batches writes and serves read requests."""

    def __init__(self, service: PersistenceService, flush_interval: float) -> None:
        super().__init__()
        self._service = service
        self._database = service._database
        self._flush_interval = flush_interval
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._bulk_writer_name: str | None = None
        self._bulk_checked = False

    # -- command intake --------------------------------------------------
    def submit(self, command: tuple) -> None:
        """Queue a command for the worker to process."""
        self._queue.put(command)

    def shutdown(self) -> None:
        """Ask the loop to flush and exit."""
        self._stop.set()
        self._queue.put(None)  # wake the loop immediately

    # -- main loop -------------------------------------------------------
    def run(self) -> None:  # noqa: D401 - QThread entry point
        while not self._stop.is_set():
            try:
                command = self._queue.get(timeout=self._flush_interval)
            except queue.Empty:
                self._flush()  # periodic auto-flush
                continue
            if command is None:  # shutdown sentinel
                break
            self._handle(command)
        # Final flush so no buffered samples are lost on shutdown.
        self._flush()

    def _handle(self, command: tuple) -> None:
        kind = command[0]
        try:
            if kind == "flush":
                self._flush()
            elif kind == "load_traffic":
                self._load_traffic(command[1])
            elif kind == "load_devices":
                self._load_devices()
            else:
                logger.debug("Unknown persistence command: %s", kind)
        except Exception:  # a bad command must not kill the thread
            logger.exception("Persistence command %s failed", kind)

    # -- writing ---------------------------------------------------------
    def _flush(self) -> None:
        items = self._service._drain_buffer()
        if not items:
            return
        db = self._database
        if db is None:
            return
        try:
            if not self._write_bulk(db, items):
                for sample in items:
                    self._write_one(db, sample)
            self._prune(db)
        except Exception:
            logger.exception("Flushing traffic samples failed")
            self._service.error.emit("Failed to persist traffic history")

    def _write_bulk(self, db: object, samples: list) -> bool:
        """Try a single bulk write; return True if one was performed."""
        if not self._bulk_checked:
            self._bulk_checked = True
            for name in _BULK_WRITE_NAMES:
                if callable(getattr(db, name, None)):
                    self._bulk_writer_name = name
                    break
        if self._bulk_writer_name is None:
            return False
        method = getattr(db, self._bulk_writer_name)
        try:
            method(samples)
            return True
        except Exception as exc:  # fall back to per-row writes
            logger.debug("Bulk write %s failed: %s", self._bulk_writer_name, exc)
            self._bulk_writer_name = None
            return False

    @staticmethod
    def _write_one(db: object, sample: object) -> None:
        """Persist a single sample, tolerating object or keyword signatures."""
        record = getattr(db, "record_traffic", None)
        if not callable(record):
            return
        # 1) Pass the sample object directly.
        try:
            record(sample)
            return
        except TypeError:
            pass
        except Exception as exc:
            logger.debug("record_traffic(sample) failed: %s", exc)
            return
        # 2) Fall back to keyword arguments built from the sample.
        kwargs = {
            "download_mbps": float(getattr(sample, "download_mbps", 0.0) or 0.0),
            "upload_mbps": float(getattr(sample, "upload_mbps", 0.0) or 0.0),
        }
        for field in _OPTIONAL_SAMPLE_FIELDS:
            if hasattr(sample, field):
                kwargs[field] = getattr(sample, field)
        try:
            record(**kwargs)
            return
        except TypeError:
            pass
        except Exception as exc:
            logger.debug("record_traffic(**kwargs) failed: %s", exc)
            return
        # 3) Last resort: only the two rates.
        try:
            record(
                download_mbps=kwargs["download_mbps"],
                upload_mbps=kwargs["upload_mbps"],
            )
        except Exception as exc:
            logger.debug("record_traffic minimal failed: %s", exc)

    @staticmethod
    def _prune(db: object) -> None:
        """Trim old traffic rows if the database supports it."""
        prune = getattr(db, "prune_traffic_history", None)
        if callable(prune):
            try:
                prune()
            except Exception as exc:
                logger.debug("prune_traffic_history failed: %s", exc)

    # -- reading ---------------------------------------------------------
    def _load_traffic(self, limit: int) -> None:
        records = self._read(
            self._database, "get_traffic_history", limit=limit
        )
        self._service.traffic_history_loaded.emit(records)

    def _load_devices(self) -> None:
        records = self._read(self._database, "get_devices")
        self._service.devices_loaded.emit(records)

    @staticmethod
    def _read(db: object, method_name: str, limit: int | None = None) -> list:
        """Call a getter tolerating limit-kw, positional, or no-arg forms."""
        if db is None:
            return []
        getter = getattr(db, method_name, None)
        if not callable(getter):
            return []
        attempts: tuple[tuple[tuple, dict], ...]
        if limit is None:
            attempts = (((), {}),)
        else:
            attempts = (((), {"limit": limit}), ((limit,), {}), ((), {}))
        for args, kwargs in attempts:
            try:
                result = getter(*args, **kwargs)
                return list(result) if result is not None else []
            except TypeError:
                continue
            except Exception as exc:
                logger.debug("%s read failed: %s", method_name, exc)
                return []
        return []
