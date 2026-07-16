"""Packet capture (network layer).
​
A thin, **UI-agnostic** wrapper around Scapy's asynchronous sniffer. It knows
nothing about Qt, threads-for-the-GUI, or the database -- it simply sniffs
packets and hands each one back as a plain :class:`CapturedPacket`.
​
Design
------
- **Soft dependency.** Scapy is imported defensively at module load. If it (or
  the underlying pcap driver) is missing, the rest of the app still runs;
  :meth:`PacketCapture.is_available` reports ``False`` and :meth:`capture`
  raises :class:`PacketCaptureError`.
- **Cooperative stop.** :meth:`capture` runs until a caller-provided
  ``threading.Event`` is set, so a service thread can stop it cleanly.
- **Decoupled output.** Each packet is normalised into a :class:`CapturedPacket`
  dataclass, so consumers never import or touch Scapy types.
​
This module performs privileged packet capture and is intended only for
networks you own or are authorised to monitor.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# -- Soft Scapy import ---------------------------------------------------
# Imported at load time but guarded: a missing dependency must never crash
# application startup (mirrors the project's graceful-degradation principle).
try:  # pragma: no cover - depends on the runtime environment
    from scapy.all import (
        ARP,
        DNS,
        ICMP,
        IP,
        IPv6,
        TCP,
        UDP,
        AsyncSniffer,
        Ether,
        hexdump,
    )

    _SCAPY_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    logger.info("Scapy unavailable, packet capture disabled: %s", exc)
    ARP = DNS = ICMP = IP = IPv6 = TCP = UDP = Ether = None  # type: ignore[assignment]
    AsyncSniffer = None  # type: ignore[assignment]
    hexdump = None  # type: ignore[assignment]
    _SCAPY_AVAILABLE = False


class PacketCaptureError(RuntimeError):
    """Raised when a capture cannot start or run (e.g. Scapy missing)."""


@dataclass(slots=True)
class CapturedPacket:
    """A single captured packet, normalised for the UI/service layers.

    Attributes mirror the columns shown on the capture page; ``detail`` and
    ``hexdump`` power the detail pane and are optional (may be empty).
    """

    timestamp: float
    source: str
    destination: str
    protocol: str
    length: int
    info: str
    detail: str = ""
    hexdump: str = ""


PacketCallback = Callable[[CapturedPacket], None]


class PacketCapture:
    """Sniff packets off an interface and emit :class:`CapturedPacket` objects.

    The class is intentionally stateless between calls: each :meth:`capture`
    invocation owns its own sniffer and stops when the supplied event is set.
    """

    @staticmethod
    def is_available() -> bool:
        """Return ``True`` if Scapy (and thus capture) is importable."""
        return _SCAPY_AVAILABLE

    def capture(
        self,
        on_packet: PacketCallback,
        stop_event,
        bpf_filter: Optional[str] = None,
        interface: Optional[str] = None,
        include_detail: bool = True,
    ) -> None:
        """Capture packets until *stop_event* is set.

        Parameters
        ----------
        on_packet:
            Called once per packet with a :class:`CapturedPacket`. It is invoked
            from Scapy's sniffer thread, so the callback must be thread-safe
            (a Qt signal emission is ideal).
        stop_event:
            A ``threading.Event``; capture runs until it is set.
        bpf_filter:
            Optional Berkeley Packet Filter expression (e.g. ``"tcp port 80"``).
        interface:
            Optional interface name; ``None`` lets Scapy pick the default.
        include_detail:
            When ``True`` (default) each packet's ``detail`` and ``hexdump`` are
            pre-rendered. Set ``False`` for high-throughput captures where that
            per-packet cost is undesirable.

        Raises
        ------
        PacketCaptureError:
            If Scapy is unavailable or the sniffer fails to start.
        """
        if not _SCAPY_AVAILABLE or AsyncSniffer is None:
            raise PacketCaptureError(
                "Packet capture is unavailable (Scapy / pcap driver not installed)."
            )

        def _handle(pkt) -> None:
            try:
                on_packet(self._to_captured(pkt, include_detail))
            except Exception as cb_exc:  # never let a callback kill the sniffer
                logger.debug("Packet callback failed: %s", cb_exc)

        try:
            sniffer = AsyncSniffer(
                prn=_handle,
                filter=bpf_filter or None,
                iface=interface or None,
                store=False,
            )
            sniffer.start()
        except Exception as exc:
            raise PacketCaptureError(f"Failed to start capture: {exc}") from exc

        logger.info(
            "Packet capture started (iface=%s, filter=%r)", interface, bpf_filter
        )
        try:
            # Block cooperatively until asked to stop.
            while not stop_event.is_set():
                stop_event.wait(0.2)
        finally:
            try:
                sniffer.stop()
            except Exception as exc:  # pragma: no cover
                logger.debug("Error stopping sniffer: %s", exc)
            logger.info("Packet capture stopped")

    # -- normalisation ---------------------------------------------------
    @classmethod
    def _to_captured(cls, pkt, include_detail: bool) -> CapturedPacket:
        """Convert a raw Scapy packet into a :class:`CapturedPacket`."""
        try:
            timestamp = float(getattr(pkt, "time", None) or time.time())
        except (TypeError, ValueError):
            timestamp = time.time()

        source, destination = cls._endpoints(pkt)
        protocol = cls._protocol(pkt)

        try:
            length = int(len(pkt))
        except Exception:
            length = 0

        try:
            info = pkt.summary()
        except Exception:
            info = ""

        detail = ""
        dump = ""
        if include_detail:
            try:
                detail = pkt.show(dump=True) or ""
            except Exception:
                detail = ""
            try:
                dump = hexdump(pkt, dump=True) if hexdump is not None else ""
            except Exception:
                dump = ""

        return CapturedPacket(
            timestamp=timestamp,
            source=source,
            destination=destination,
            protocol=protocol,
            length=length,
            info=info,
            detail=detail,
            hexdump=dump,
        )

    @staticmethod
    def _endpoints(pkt) -> tuple[str, str]:
        """Best-effort (source, destination) extraction across layer types."""
        try:
            if ARP is not None and pkt.haslayer(ARP):
                return str(pkt[ARP].psrc), str(pkt[ARP].pdst)
            if IP is not None and pkt.haslayer(IP):
                return str(pkt[IP].src), str(pkt[IP].dst)
            if IPv6 is not None and pkt.haslayer(IPv6):
                return str(pkt[IPv6].src), str(pkt[IPv6].dst)
            if Ether is not None and pkt.haslayer(Ether):
                return str(pkt[Ether].src), str(pkt[Ether].dst)
        except Exception:  # pragma: no cover - malformed packet
            pass
        return "", ""

    @staticmethod
    def _protocol(pkt) -> str:
        """Return a short protocol label for the highest recognised layer."""
        try:
            if ARP is not None and pkt.haslayer(ARP):
                return "ARP"
            if DNS is not None and pkt.haslayer(DNS):
                return "DNS"
            if TCP is not None and pkt.haslayer(TCP):
                return "TCP"
            if UDP is not None and pkt.haslayer(UDP):
                return "UDP"
            if ICMP is not None and pkt.haslayer(ICMP):
                return "ICMP"
            if IPv6 is not None and pkt.haslayer(IPv6):
                return "IPv6"
            if IP is not None and pkt.haslayer(IP):
                return "IP"
            last = pkt.lastlayer()
            return getattr(last, "name", None) or pkt.name
        except Exception:  # pragma: no cover
            return ""


__all__ = ["PacketCapture", "CapturedPacket", "PacketCaptureError"]
