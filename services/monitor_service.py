"""Bandwidth monitor service.
​
Bridges the GUI-agnostic :class:`~app.network.monitor.BandwidthMonitor` to the
Qt world. It runs the (blocking) sampling loop on a background :class:`QThread`
and emits one Qt signal per sample, so the UI thread never performs network or
I/O work and stays responsive.
​
Design
------
- ``sample_ready(object)`` fires once per interval with whatever sample object
  the monitor produces (the views read ``download_mbps`` / ``upload_mbps`` off
  it via ``getattr``, so this layer stays decoupled from the concrete type).
- ``error(str)`` fires with a human-readable message if the monitor cannot be
  created or a read fails. Transient read errors are logged and retried rather
  than killing the thread.
- The service is deliberately **tolerant of the monitor's exact API**: it
  discovers the sampling method by name and constructs the monitor with or
  without an ``interface`` argument. This keeps it working regardless of the
  precise method names in ``monitor.py``.
- Interval and interface can be changed live from the GUI thread; the change is
  queued and applied safely on the worker thread.
"""
from __future__ import annotations

import inspect
import logging
import threading

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# Never sample faster than this (protects the CPU from a misconfigured interval).
_MIN_INTERVAL: float = 0.2

# Candidate method names on BandwidthMonitor that return a single sample.
_SAMPLE_METHOD_NAMES = ("sample", "read", "poll", "measure", "get_sample", "read_sample")

# Sentinel meaning "no pending interface change".
_UNSET = object()


class MonitorService(QThread):
    """Background bandwidth sampler.

    Parameters
    ----------
    interval:
        Seconds between samples (clamped to a sane minimum).
    interface:
        Optional network interface name to monitor (``None`` = all/default).
    """

    sample_ready = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        interval: float = 1.0,
        interface: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._interval = self._clamp_interval(interval)
        self._interface = interface
        self._interface_pending = _UNSET  # queued interface change
        self._monitor = None
        self._sample_method = None
        self._sample_takes_arg: bool | None = None  # cached method arity
        self._error_emitted = False

    # -- public API (call from any thread) -------------------------------
    def set_interval(self, interval: float) -> None:
        """Change the sampling interval; applied on the next loop iteration."""
        with self._lock:
            self._interval = self._clamp_interval(interval)

    def set_interface(self, interface: str | None) -> None:
        """Change the monitored interface; applied on the worker thread."""
        with self._lock:
            self._interface_pending = interface

    def stop(self) -> None:
        """Signal the loop to end and wait for the thread to finish."""
        self._stop.set()
        if self.isRunning():
            self.wait(3000)

    # -- QThread entry point ---------------------------------------------
    def run(self) -> None:  # noqa: D401 - QThread entry point
        """Sample bandwidth in a loop until stopped (worker thread)."""
        try:
            from app.network.monitor import BandwidthMonitor
        except Exception as exc:  # psutil missing / import failure
            logger.warning("BandwidthMonitor unavailable: %s", exc)
            self.error.emit(f"Bandwidth monitor unavailable: {exc}")
            return

        if not self._init_monitor(BandwidthMonitor):
            return

        while not self._stop.is_set():
            interval = self._apply_pending_and_get_interval()
            try:
                sample = self._read_sample()
            except Exception as exc:  # a bad read must not kill the thread
                logger.exception("Bandwidth sample failed")
                if not self._error_emitted:
                    self.error.emit(f"Bandwidth sampling error: {exc}")
                    self._error_emitted = True
                self._stop.wait(interval)
                continue

            if sample is not None:
                self._error_emitted = False
                self.sample_ready.emit(sample)

            # Interruptible sleep: returns immediately when stop() is called.
            self._stop.wait(interval)

        self._shutdown_monitor()

    # -- monitor lifecycle -----------------------------------------------
    def _init_monitor(self, monitor_cls) -> bool:
        """Instantiate the monitor and resolve its sampling method."""
        with self._lock:
            interface = self._interface
        try:
            self._monitor = self._construct(monitor_cls, interface)
        except Exception as exc:
            logger.exception("Could not create BandwidthMonitor")
            self.error.emit(f"Could not start bandwidth monitor: {exc}")
            return False

        if not self._bind_sample_method(self._monitor):
            msg = "BandwidthMonitor exposes no recognised sampling method"
            logger.error(msg)
            self.error.emit(msg)
            return False
        return True

    @staticmethod
    def _construct(monitor_cls, interface: str | None):
        """Construct the monitor, tolerating either an interface arg or none."""
        try:
            return monitor_cls(interface=interface)
        except TypeError:
            monitor = monitor_cls()
            setter = getattr(monitor, "set_interface", None)
            if interface is not None and callable(setter):
                try:
                    setter(interface)
                except Exception as exc:  # non-fatal; monitor default is fine
                    logger.debug("Could not set interface on monitor: %s", exc)
            return monitor

    def _bind_sample_method(self, monitor) -> bool:
        """Resolve the sampling method and cache whether it needs an argument.

        Returns ``True`` if a usable method was found. Arity is detected once
        here (rather than per-read) so we never accidentally call the sampler
        twice, which would corrupt delta-based bandwidth math.
        """
        for name in _SAMPLE_METHOD_NAMES:
            method = getattr(monitor, name, None)
            if callable(method):
                self._sample_method = method
                self._sample_takes_arg = self._detect_takes_arg(method)
                logger.info(
                    "Using BandwidthMonitor.%s() (takes_arg=%s)",
                    name,
                    self._sample_takes_arg,
                )
                return True
        self._sample_method = None
        self._sample_takes_arg = None
        return False

    @staticmethod
    def _detect_takes_arg(method) -> bool | None:
        """Return True/False if the method requires a positional arg, else None."""
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            return None  # unknown; caller falls back to try/except
        for param in sig.parameters.values():
            if (
                param.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
                and param.default is inspect.Parameter.empty
            ):
                return True
        return False

    def _read_sample(self):
        """Read one sample, respecting the sampler's detected arity."""
        method = self._sample_method
        if method is None:
            return None

        takes_arg = self._sample_takes_arg
        if takes_arg is True:
            with self._lock:
                interval = self._interval
            return method(interval)
        if takes_arg is False:
            return method()

        # Arity unknown: try no-arg, then fall back to interval-arg once.
        try:
            sample = method()
            self._sample_takes_arg = False  # cache success for next time
            return sample
        except TypeError:
            with self._lock:
                interval = self._interval
            sample = method(interval)
            self._sample_takes_arg = True
            return sample

    def _apply_pending_and_get_interval(self) -> float:
        """Apply any queued interface change and return the current interval."""
        with self._lock:
            interval = self._interval
            pending = self._interface_pending
            self._interface_pending = _UNSET
            if pending is not _UNSET:
                self._interface = pending
        if pending is not _UNSET:
            self._change_interface(pending)
        return interval

    def _change_interface(self, interface: str | None) -> None:
        """Apply an interface change to the live monitor (worker thread)."""
        monitor = self._monitor
        if monitor is None:
            return
        setter = getattr(monitor, "set_interface", None)
        if callable(setter):
            try:
                setter(interface)
                logger.info("Monitor interface changed to %s", interface or "all")
                return
            except Exception as exc:
                logger.debug("set_interface failed, recreating monitor: %s", exc)

        # Fall back to recreating the monitor with the new interface.
        try:
            from app.network.monitor import BandwidthMonitor

            self._monitor = self._construct(BandwidthMonitor, interface)
            self._bind_sample_method(self._monitor)
            logger.info("Monitor recreated for interface %s", interface or "all")
        except Exception as exc:
            logger.exception("Could not switch monitor interface")
            self.error.emit(f"Could not switch interface: {exc}")

    def _shutdown_monitor(self) -> None:
        """Release monitor resources if it exposes a stop/close hook."""
        monitor = self._monitor
        if monitor is None:
            return
        for name in ("stop", "close", "shutdown"):
            hook = getattr(monitor, name, None)
            if callable(hook):
                try:
                    hook()
                except Exception as exc:  # never raise during shutdown
                    logger.debug("Monitor %s() failed: %s", name, exc)
                break

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _clamp_interval(interval: float) -> float:
        """Clamp *interval* to a safe minimum, falling back to 1.0 on error."""
        try:
            return max(_MIN_INTERVAL, float(interval))
        except (TypeError, ValueError):
            return 1.0


__all__ = ["MonitorService"]