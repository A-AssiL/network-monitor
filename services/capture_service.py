"""Packet capture service (bridge layer).
​
Runs :class:`~app.network.packet_capture.PacketCapture` on a background
``QThread`` and re-publishes results to the UI exclusively through Qt signals,
so the GUI thread is never blocked and the network layer stays Qt-agnostic.
​
Why batching
------------
On a busy link the sniffer can deliver thousands of packets per second. If we
emitted one Qt signal per packet (and wrote one DB row per packet on the GUI
thread), the GUI event queue would flood and the window would freeze. Instead:
​
- the worker **buffers** captured packets in a thread-safe, memory-capped
  ``deque`` (filled from Scapy's sniffer thread), and
- a **QTimer on the service** (GUI thread) drains that buffer a few times a
  second and emits a single **batched** signal, persisting the whole batch in
  one transaction.
​
This keeps the UI responsive no matter how fast packets arrive.
​
Contract
--------
Inbound (called by the main window / capture page):
    - :meth:`start` / :meth:`start_capture` -- begin capturing.
    - :meth:`stop` / :meth:`stop_capture`   -- end the current capture.
​
Outbound signals (connected to the capture page / main window):
    - :attr:`capture_started` ()          -- a capture began.
    - :attr:`capture_stopped` ()          -- the capture ended.
    - :attr:`packets_captured` (object)   -- a list[CapturedPacket] batch.
    - :attr:`error` (str)                 -- a capture error message.
​
If an optional ``database`` with a ``record_packets`` (bulk) or ``record_packet``
method is supplied, each batch is offered to it for persistence (best-effort).
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from app.network.packet_capture import PacketCapture, PacketCaptureError

logger = logging.getLogger(__name__)

# shiboken6.isValid guards against touching a Qt object whose underlying C++
# instance has already been deleted (a classic QThread-teardown crash). If
# shiboken6 is unavailable we optimistically assume the object is valid.
try:  # pragma: no cover - depends on the runtime environment
    import shiboken6

    def qt_is_valid(obj: object) -> bool:
        try:
            return bool(shiboken6.isValid(obj))
        except Exception:
            return True

except Exception:  # pragma: no cover
    def qt_is_valid(obj: object) -> bool:
        return True


class _CaptureWorker(QThread):
    """Background thread that drives a single capture session.
​
    Packets are **buffered** as they arrive (from Scapy's sniffer thread) and
    handed to the service in batches via :meth:`drain`; the worker never emits
    a Qt signal per packet. A fatal problem is reported through :attr:`error`.
    """

    error = Signal(str)

    # Hard cap on buffered packets. If the UI cannot keep up, the oldest
    # packets are dropped (counted) so memory stays bounded.
    _MAX_BUFFER = 50_000

    def __init__(
        self,
        bpf_filter: Optional[str],
        interface: Optional[str],
        include_detail: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bpf_filter = bpf_filter
        self._interface = interface
        self._include_detail = include_detail
        self._stop_event = threading.Event()
        self._capture = PacketCapture()
        self._buffer: deque = deque(maxlen=self._MAX_BUFFER)
        self._buffer_lock = threading.Lock()
        self._dropped = 0

    def run(self) -> None:  # executed on the worker thread
        try:
            self._capture.capture(
                on_packet=self._buffer_packet,
                stop_event=self._stop_event,
                bpf_filter=self._bpf_filter,
                interface=self._interface,
                include_detail=self._include_detail,
            )
        except PacketCaptureError as exc:
            self.error.emit(str(exc))
        except Exception as exc:  # pragma: no cover - unexpected sniffer failure
            logger.exception("Capture worker crashed")
            self.error.emit(f"Capture failed: {exc}")

    def _buffer_packet(self, packet: object) -> None:
        # Invoked from Scapy's sniffer thread: just buffer the packet. The
        # service drains this buffer on a timer, so we never emit one Qt
        # signal per packet (which would flood and freeze the GUI thread).
        with self._buffer_lock:
            if len(self._buffer) == self._buffer.maxlen:
                # deque will evict the oldest on append; record the loss.
                self._dropped += 1
            self._buffer.append(packet)

    def drain(self) -> list:
        """Atomically take everything buffered so far (thread-safe)."""
        with self._buffer_lock:
            if not self._buffer:
                return []
            batch = list(self._buffer)
            self._buffer.clear()
            return batch

    def dropped_count(self) -> int:
        with self._buffer_lock:
            return self._dropped

    def stop(self) -> None:
        """Ask the capture loop to finish (thread-safe)."""
        self._stop_event.set()


class CaptureService(QObject):
    """GUI-facing packet capture service.

    Owns at most one :class:`_CaptureWorker` at a time and exposes a small,
    signal-based API for the capture page. Packets are delivered in batches to
    keep the GUI responsive under heavy traffic.
    """

    capture_started = Signal()
    capture_stopped = Signal()
    packets_captured = Signal(object)  # emits list[CapturedPacket]
    error = Signal(str)

    # How often to drain the worker buffer and repaint (milliseconds).
    _FLUSH_INTERVAL_MS = 250

    def __init__(self, database: Any | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._database = database
        self._worker: Optional[_CaptureWorker] = None
        # Timer lives on the service's (GUI) thread; drives batched delivery.
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self._FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush)

    # -- availability / state -------------------------------------------
    @staticmethod
    def is_available() -> bool:
        """Return ``True`` if the capture backend (Scapy) is installed."""
        return PacketCapture.is_available()

    def is_capturing(self) -> bool:
        """Return ``True`` while a capture worker is running."""
        return self._worker is not None and self._worker.isRunning()

    # -- start ----------------------------------------------------------
    def start(self, config: object = None) -> None:
        """Start a capture from a config dict (as emitted by the capture page).

        Accepts ``{"bpf_filter": str|None, "interface": str|None,
        "include_detail": bool}``; anything missing falls back to sane
        defaults (capture all / default interface / details on).
        """
        bpf_filter: Optional[str] = None
        interface: Optional[str] = None
        include_detail = True
        if isinstance(config, dict):
            bpf_filter = config.get("bpf_filter") or None
            interface = config.get("interface") or None
            include_detail = bool(config.get("include_detail", True))
        self.start_capture(bpf_filter, interface, include_detail)

    def start_capture(
        self,
        bpf_filter: Optional[str] = None,
        interface: Optional[str] = None,
        include_detail: bool = True,
    ) -> None:
        """Begin capturing on a background thread."""
        if not self.is_available():
            self.error.emit(
                "Packet capture unavailable (Scapy / pcap driver not installed)."
            )
            return
        if self.is_capturing():
            logger.debug("Capture already running; ignoring start request")
            return

        worker = _CaptureWorker(bpf_filter, interface, include_detail)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()
        self._flush_timer.start()
        logger.info("Capture service started")
        self.capture_started.emit()

    # -- stop -----------------------------------------------------------
    def stop(self) -> None:
        """Alias for :meth:`stop_capture` (matches the page's stop_requested)."""
        self.stop_capture()

    def stop_capture(self) -> None:
        """Stop the current capture and wait briefly for the thread to end."""
        worker = self._worker
        if worker is None:
            return
        worker.stop()
        # Give the sniffer a moment to unwind. capture_stopped and the final
        # buffer flush are handled in the worker's finished handler.
        if not worker.wait(3000):
            logger.warning("Capture worker did not stop within timeout")

    # -- batched delivery -----------------------------------------------
    def _flush(self) -> None:
        """Drain the worker buffer and publish one batch (GUI thread)."""
        worker = self._worker
        if worker is None:
            return
        batch = worker.drain()
        if not batch:
            return
        self._persist(batch)
        self.packets_captured.emit(batch)

    def _persist(self, packets: list) -> None:
        """Best-effort persistence of a whole batch (bulk if supported)."""
        if self._database is None:
            return
        bulk = getattr(self._database, "record_packets", None)
        if callable(bulk):
            try:
                bulk(packets)
                return
            except Exception as exc:  # pragma: no cover - persistence is optional
                logger.debug("Bulk packet persist failed (%s); falling back", exc)
        recorder = getattr(self._database, "record_packet", None)
        if callable(recorder):
            for packet in packets:
                try:
                    recorder(packet)
                except Exception as exc:  # pragma: no cover
                    logger.debug("Failed to persist packet: %s", exc)

    # -- worker callbacks -----------------------------------------------
    def _on_worker_error(self, message: str) -> None:
        self.error.emit(message)

    def _on_worker_finished(self) -> None:
        """Stop the timer, flush the tail, clean up, then signal stopped."""
        self._flush_timer.stop()
        worker = self._worker
        self._worker = None
        if worker is not None:
            # Publish anything captured just before shutdown.
            batch = worker.drain()
            if batch:
                self._persist(batch)
                self.packets_captured.emit(batch)
            dropped = worker.dropped_count()
            if dropped:
                logger.warning(
                    "Capture dropped %d packet(s): UI/buffer could not keep up",
                    dropped,
                )
            if qt_is_valid(worker):
                worker.deleteLater()
        logger.info("Capture service stopped")
        self.capture_stopped.emit()


__all__ = ["CaptureService"]