"""
ARP-based network discovery.

Discovers devices on the local subnet by broadcasting ARP "who-has" requests
with :mod:`scapy` and collecting the replies. For every responding host it
gathers:
​
- **IP address**   -- from the ARP reply.
- **MAC address**  -- from the ARP reply (normalized to lower-case colon form).
- **Hostname**     -- via reverse DNS (:mod:`app.network.hostname`).
- **Vendor**       -- via MAC/OUI lookup (:mod:`app.network.vendor_lookup`).
- **Online status**-- a host that answers the ARP scan is considered online.
​
Platform notes
--------------
- ARP scanning requires **raw socket / packet access**. On Windows this means
  running as Administrator with **Npcap** installed; on Linux it needs root
  (or ``CAP_NET_RAW``). Missing privileges raise :class:`ScannerError` with a
  clear message rather than crashing the scan thread.
- This module is GUI-agnostic and must never import from :mod:`app.ui`. The
  blocking :meth:`ArpScanner.scan` is expected to run inside a background
  worker (QThread/asyncio); a ``threading.Event`` allows cancellation.
​
Typical usage
-------------
    >>> scanner = ArpScanner()
    >>> devices = scanner.scan()          # auto-detect the local subnet
    >>> for device in devices:
    ...     print(device.ip, device.mac, device.hostname, device.vendor)
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import hostname as hostname_resolver

# Vendor lookup is a sibling module. Import softly so scanner.py remains
# usable even before vendor_lookup.py is added to the project; when present,
# it is used automatically.
try:  # pragma: no cover - exercised at integration time
    from .vendor_lookup import VendorLookup
except Exception:  # ImportError, or module not yet implemented
    VendorLookup = None  # type: ignore[assignment,misc]

__all__ = ["DiscoveredDevice", "ArpScanner", "ScannerError"]

logger = logging.getLogger(__name__)

# Default per-scan timeout (seconds) to wait for ARP replies.
DEFAULT_TIMEOUT: float = 3.0
# Default number of times each ARP request is retried.
DEFAULT_RETRIES: int = 1


class ScannerError(RuntimeError):
    """Raised when a scan cannot be performed (privileges, backend, etc.)."""


@dataclass(slots=True)
class DiscoveredDevice:
    """
    A single device found during a scan.

    Attributes
    ----------
    ip:
        The device's IPv4 address.
    mac:
        The device's MAC address (lower-case, colon-separated).
    hostname:
        Reverse-DNS hostname, or ``None`` if unavailable.
    vendor:
        Manufacturer resolved from the MAC OUI, or ``None`` if unknown.
    online:
        Whether the device responded to this scan.
    last_seen:
        Wall-clock time (``time.time()``) when the device last responded.
    """

    ip: str
    mac: str
    hostname: str | None = None
    vendor: str | None = None
    online: bool = True
    last_seen: float = field(default_factory=time.time)


class ArpScanner:
    """
    Discovers devices on the local network using ARP.

    Parameters
    ----------
    timeout:
        Seconds to wait for ARP replies per attempt.
    retries:
        Number of times to re-send unanswered ARP requests.
    vendor_lookup:
        Optional :class:`~app.network.vendor_lookup.VendorLookup` instance.
        If omitted, one is created automatically when the module is present.
    resolve_hostnames:
        Whether to perform reverse-DNS resolution for discovered hosts.
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        vendor_lookup: "VendorLookup | None" = None,
        resolve_hostnames: bool = True,
    ) -> None:
        self._timeout = timeout
        self._retries = retries
        self._resolve_hostnames = resolve_hostnames
        self._stop_event = threading.Event()

        if vendor_lookup is not None:
            self._vendor_lookup = vendor_lookup
        elif VendorLookup is not None:
            try:
                self._vendor_lookup = VendorLookup()
            except Exception as exc:  # OUI data missing, etc.
                logger.warning("Vendor lookup unavailable: %s", exc)
                self._vendor_lookup = None
        else:
            self._vendor_lookup = None

    # -- subnet detection ------------------------------------------------

    @staticmethod
    def default_subnet() -> str:
        """
        Best-effort detection of the local IPv4 subnet in CIDR form.

        Determines the primary outbound IP (without sending traffic) and
        assumes a /24 network, which is by far the most common LAN layout.
        Falls back to ``192.168.1.0/24`` if detection fails.
        """
        local_ip = "127.0.0.1"
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Connecting a UDP socket does not send packets but populates the
            # kernel's chosen source address for the route to a public IP.
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
        except OSError as exc:
            logger.warning("Could not detect local IP, falling back: %s", exc)
        finally:
            sock.close()

        try:
            network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
            return str(network)
        except ValueError:
            return "192.168.1.0/24"

    # -- lifecycle -------------------------------------------------------

    def stop(self) -> None:
        """Request cancellation of an in-progress scan."""
        self._stop_event.set()

    # -- scanning --------------------------------------------------------

    def scan(
        self,
        subnet: str | None = None,
        on_device: Callable[[DiscoveredDevice], None] | None = None,
    ) -> list[DiscoveredDevice]:
        """
        Scan *subnet* for devices and return them enriched with metadata.

        Parameters
        ----------
        subnet:
            Target network in CIDR form (e.g. ``"192.168.1.0/24"``). When
            ``None``, :meth:`default_subnet` is used.
        on_device:
            Optional callback invoked once per discovered device -- useful for
            streaming rows into the Devices table as they are enriched.

        Returns
        -------
        list[DiscoveredDevice]
            All devices that responded, sorted by IP address.

        Raises
        ------
        ScannerError
            If scapy is unavailable or the process lacks the privileges /
            packet-capture backend needed to send ARP requests.
        """
        self._stop_event.clear()
        target = subnet or self.default_subnet()
        logger.info("Starting ARP scan of %s", target)

        answered = self._arp_request(target)
        if self._stop_event.is_set():
            logger.info("Scan cancelled before enrichment")
            return []

        devices = self._build_devices(answered)
        devices = self._enrich(devices)

        devices.sort(key=lambda d: ipaddress.ip_address(d.ip))

        if on_device is not None:
            for device in devices:
                try:
                    on_device(device)
                except Exception as exc:  # never let UI callback break scan
                    logger.exception("on_device callback raised: %s", exc)

        logger.info("ARP scan complete: %d device(s) found", len(devices))
        return devices

    # -- internals -------------------------------------------------------

    def _arp_request(self, target: str) -> list[tuple[str, str]]:
        """
        Broadcast ARP requests to *target* and return (ip, mac) pairs.

        Scapy is imported lazily so that importing this module never requires
        scapy to be installed, and so privilege/backend errors surface here
        as a clean :class:`ScannerError`.
        """
        try:
            from scapy.layers.l2 import ARP, Ether
            from scapy.sendrecv import srp
        except Exception as exc:  # scapy missing / import failure
            raise ScannerError(
                "Scapy is required for ARP scanning but is unavailable. "
                "Install it with 'pip install scapy'."
            ) from exc

        packet = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=target)

        try:
            answered, _unanswered = srp(
                packet,
                timeout=self._timeout,
                retry=self._retries,
                verbose=False,
            )
        except PermissionError as exc:
            raise ScannerError(
                "Insufficient privileges for ARP scanning. Run as "
                "Administrator (Windows, with Npcap installed) or root (Linux)."
            ) from exc
        except OSError as exc:
            raise ScannerError(
                "Could not send ARP requests. On Windows, ensure Npcap is "
                f"installed and the interface is up. Details: {exc}"
            ) from exc

        results: list[tuple[str, str]] = []
        for _sent, received in answered:
            results.append((received.psrc, self._normalize_mac(received.hwsrc)))
        return results

    def _build_devices(
        self, answered: list[tuple[str, str]]
    ) -> list[DiscoveredDevice]:
        """Convert raw (ip, mac) pairs into de-duplicated device records."""
        now = time.time()
        by_ip: dict[str, DiscoveredDevice] = {}
        for ip, mac in answered:
            by_ip[ip] = DiscoveredDevice(
                ip=ip, mac=mac, online=True, last_seen=now
            )
        return list(by_ip.values())

    def _enrich(
        self, devices: list[DiscoveredDevice]
    ) -> list[DiscoveredDevice]:
        """Populate hostname and vendor fields for each device."""
        if not devices:
            return devices

        # Hostnames: resolved in parallel with per-host timeouts.
        if self._resolve_hostnames:
            ips = [device.ip for device in devices]
            try:
                names = hostname_resolver.resolve_hostnames(ips)
            except Exception as exc:  # resolver must never break a scan
                logger.warning("Hostname resolution failed: %s", exc)
                names = {}
            for device in devices:
                device.hostname = names.get(device.ip)

        # Vendors: cheap in-memory OUI lookup.
        if self._vendor_lookup is not None:
            for device in devices:
                try:
                    device.vendor = self._vendor_lookup.lookup(device.mac)
                except Exception as exc:  # keep enrichment best-effort
                    logger.debug("Vendor lookup failed for %s: %s", device.mac, exc)

        return devices

    @staticmethod
    def _normalize_mac(mac: str) -> str:
        """Normalize a MAC address to lower-case, colon-separated form."""
        return mac.strip().lower().replace("-", ":")