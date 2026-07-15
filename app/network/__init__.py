"""
Network layer (domain logic).
​
Provides device discovery (ARP scanning), local bandwidth monitoring,
hostname resolution, and MAC/OUI vendor lookup. This layer is GUI-agnostic
and must never import from :mod:`app.ui`.
​
Public API
----------
- :class:`~app.network.scanner.ArpScanner` / :class:`DiscoveredDevice` /
  :class:`ScannerError` -- discover devices on the local subnet.
- :class:`~app.network.monitor.BandwidthMonitor` / :class:`BandwidthSample`
  -- monitor this computer's upload/download throughput.
- :func:`~app.network.hostname.resolve_hostname` /
  :func:`~app.network.hostname.resolve_hostnames` -- reverse-DNS lookups.
- :class:`~app.network.vendor_lookup.VendorLookup` -- MAC -> vendor lookup.
​
Imports are kept lazy where dependencies are optional: importing this package
does not require scapy to be installed. Only :mod:`hostname` and :mod:`monitor`
symbols (standard library + psutil) are eagerly re-exported; scanner and
vendor-lookup symbols are resolved on first attribute access via
:func:`__getattr__` so a missing scapy install never breaks ``import
app.network``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .hostname import resolve_hostname, resolve_hostnames
from .monitor import BandwidthMonitor, BandwidthSample

__all__ = [
    "ArpScanner",
    "DiscoveredDevice",
    "ScannerError",
    "BandwidthMonitor",
    "BandwidthSample",
    "resolve_hostname",
    "resolve_hostnames",
    "VendorLookup",
]

# Map lazily-exported names to their defining submodule. These pull in optional
# dependencies (scapy for the scanner), so they are only imported on demand.
_LAZY_EXPORTS: dict[str, str] = {
    "ArpScanner": "scanner",
    "DiscoveredDevice": "scanner",
    "ScannerError": "scanner",
    "VendorLookup": "vendor_lookup",
}


def __getattr__(name: str) -> Any:
    """Resolve lazily-exported symbols on first access (PEP 562)."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f"{__name__}.{module_name}")
    return getattr(module, name)


def __dir__() -> list[str]:
    """Include lazily-exported names in ``dir(app.network)``."""
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    # Give type checkers/IDEs the full picture without triggering the optional
    # (scapy) imports at runtime.
    from .scanner import ArpScanner, DiscoveredDevice, ScannerError
    from .vendor_lookup import VendorLookup