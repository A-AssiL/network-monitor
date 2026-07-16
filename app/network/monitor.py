"""Local bandwidth monitoring.
​
Measures *this computer's* upload and download throughput by sampling the
cumulative byte counters exposed by :mod:`psutil` and converting the delta
between two samples into a rate (Mbps).
​
Design
------
- Rates are **delta-based**: each sample compares the current cumulative
  counters against the previous reading over the actual elapsed wall-clock
  time, which keeps values accurate even if the sampling interval drifts.
- The monitor can track **all interfaces aggregated** (default) or a single
  named interface (driven by ``config.json`` / the Settings page).
- This module is GUI-agnostic and must never import from :mod:`app.ui`.
  The continuous loop (:meth:`BandwidthMonitor.stream`) is designed to be
  run inside a background worker (QThread/asyncio); a ``threading.Event`` is
  used to stop it cleanly.
​
Typical usage
-------------
    >>> monitor = BandwidthMonitor()
    >>> monitor.sample()          # first call primes the counters
    BandwidthSample(download_mbps=0.0, upload_mbps=0.0, ...)
    >>> time.sleep(1)
    >>> monitor.sample()          # subsequent calls report real rates
    BandwidthSample(download_mbps=12.34, upload_mbps=1.05, ...)
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import psutil

__all__ = ["BandwidthSample", "BandwidthMonitor"]

logger = logging.getLogger(__name__)

# Bits per byte, and bits per megabit -- used to convert bytes/sec to Mbps.
_BITS_PER_BYTE: int = 8
_BITS_PER_MEGABIT: int = 1_000_000

DEFAULT_INTERVAL: float = 1.0


@dataclass(frozen=True, slots=True)
class BandwidthSample:
    """A single bandwidth reading.

    Attributes
    ----------
    timestamp:
        Wall-clock time (``time.time()``) when the sample was taken.
    download_mbps:
        Download (received) throughput since the previous sample, in Mbps.
    upload_mbps:
        Upload (sent) throughput since the previous sample, in Mbps.
    bytes_recv:
        Cumulative bytes received at sample time (raw counter).
    bytes_sent:
        Cumulative bytes sent at sample time (raw counter).
    interface:
        The interface being monitored, or ``None`` for the aggregate of all
        interfaces.
    """

    timestamp: float
    download_mbps: float
    upload_mbps: float
    bytes_recv: int
    bytes_sent: int
    interface: str | None = None


@dataclass
class _CounterReading:
    """Internal snapshot of raw counters at a moment in time."""

    timestamp: float
    bytes_recv: int
    bytes_sent: int


class BandwidthMonitor:
    """Samples local network throughput using :mod:`psutil`.

    Parameters
    ----------
    interface:
        Name of the interface to monitor (e.g. ``"Wi-Fi"``, ``"Ethernet"``).
        If ``None`` (default), counters for all interfaces are aggregated.
    """

    def __init__(self, interface: str | None = None) -> None:
        self._interface: str | None = interface
        self._previous: _CounterReading | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    # -- configuration ---------------------------------------------------
    @property
    def interface(self) -> str | None:
        """The interface currently being monitored (``None`` = aggregate)."""
        return self._interface

    def set_interface(self, interface: str | None) -> None:
        """Switch the monitored interface.

        Resets the internal baseline so the next :meth:`sample` re-primes the
        counters instead of reporting a bogus spike from mismatched readings.
        """
        with self._lock:
            self._interface = interface
            self._previous = None
        logger.info("Bandwidth monitor interface set to %s", interface or "<all>")

    @staticmethod
    def available_interfaces() -> list[str]:
        """Return the names of interfaces exposing IO counters."""
        try:
            return sorted(psutil.net_io_counters(pernic=True).keys())
        except Exception as exc:  # defensive: platform quirks
            logger.warning("Could not enumerate interfaces: %s", exc)
            return []

    # -- reading counters ------------------------------------------------
    def _read_counters(self) -> _CounterReading | None:
        """Read the current cumulative byte counters for the target interface.

        Returns ``None`` if the configured interface is unavailable.
        """
        now = time.time()
        if self._interface is None:
            counters = psutil.net_io_counters(pernic=False)
            if counters is None:
                return None
            return _CounterReading(now, counters.bytes_recv, counters.bytes_sent)

        per_nic = psutil.net_io_counters(pernic=True)
        counters = per_nic.get(self._interface)
        if counters is None:
            logger.warning(
                "Interface %r not found; available: %s",
                self._interface,
                list(per_nic.keys()),
            )
            return None
        return _CounterReading(now, counters.bytes_recv, counters.bytes_sent)

    # -- sampling --------------------------------------------------------
    def reset(self) -> None:
        """Discard the baseline so the next sample re-primes the counters."""
        with self._lock:
            self._previous = None

    def sample(self) -> BandwidthSample | None:
        """Take a bandwidth sample.

        The first call after construction/reset primes the counters and
        reports ``0.0`` rates. Each subsequent call computes throughput from
        the delta since the previous call.

        Returns
        -------
        BandwidthSample | None
            The sample, or ``None`` if counters could not be read (e.g. the
            configured interface disappeared).
        """
        with self._lock:
            current = self._read_counters()
            if current is None:
                return None
            previous = self._previous
            self._previous = current

        if previous is None:
            # First reading: nothing to diff against yet.
            return BandwidthSample(
                timestamp=current.timestamp,
                download_mbps=0.0,
                upload_mbps=0.0,
                bytes_recv=current.bytes_recv,
                bytes_sent=current.bytes_sent,
                interface=self._interface,
            )

        elapsed = current.timestamp - previous.timestamp
        if elapsed <= 0:
            # Clock did not advance (or went backwards); avoid divide-by-zero.
            elapsed = DEFAULT_INTERVAL

        download_mbps = self._to_mbps(
            current.bytes_recv - previous.bytes_recv, elapsed
        )
        upload_mbps = self._to_mbps(
            current.bytes_sent - previous.bytes_sent, elapsed
        )
        return BandwidthSample(
            timestamp=current.timestamp,
            download_mbps=download_mbps,
            upload_mbps=upload_mbps,
            bytes_recv=current.bytes_recv,
            bytes_sent=current.bytes_sent,
            interface=self._interface,
        )

    @staticmethod
    def _to_mbps(delta_bytes: int, elapsed_seconds: float) -> float:
        """Convert a byte delta over an interval into megabits per second.

        Negative deltas (counter reset/wrap, e.g. after sleep or interface
        restart) are clamped to ``0.0`` rather than reported as huge spikes.
        """
        if delta_bytes < 0:
            return 0.0
        bits = delta_bytes * _BITS_PER_BYTE
        return round(bits / elapsed_seconds / _BITS_PER_MEGABIT, 2)

    # -- continuous streaming (for background workers) -------------------
    def stream(
        self,
        interval: float = DEFAULT_INTERVAL,
        on_sample: Callable[[BandwidthSample], None] | None = None,
    ) -> Iterator[BandwidthSample]:
        """Continuously yield samples every *interval* seconds until stopped.

        Intended to run inside a background worker (QThread/asyncio). Call
        :meth:`stop` from another thread to end the loop cleanly.

        Parameters
        ----------
        interval:
            Seconds between samples. Defaults to 1s per the spec.
        on_sample:
            Optional callback invoked with each sample (in addition to
            yielding it) -- convenient for emitting a Qt signal.

        Yields
        ------
        BandwidthSample
            One sample per interval.
        """
        self._stop_event.clear()
        logger.info(
            "Starting bandwidth stream (interface=%s, interval=%.2fs)",
            self._interface or "<all>",
            interval,
        )
        # Prime the counters so the first *yielded* sample is a real rate.
        self.sample()
        while not self._stop_event.is_set():
            # Wait returns True if stopped during the interval -> exit promptly.
            if self._stop_event.wait(timeout=interval):
                break
            sample = self.sample()
            if sample is None:
                continue
            if on_sample is not None:
                try:
                    on_sample(sample)
                except Exception as exc:  # never let a callback kill the loop
                    logger.exception("on_sample callback raised: %s", exc)
            yield sample
        logger.info("Bandwidth stream stopped")

    def stop(self) -> None:
        """Signal :meth:`stream` to stop after its current wait."""
        self._stop_event.set()
