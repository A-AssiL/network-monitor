"""
Database models.
​
Typed :mod:`dataclasses` mirroring the tables defined in
:mod:`app.database.database`. They give the rest of the application a strongly
typed, self-documenting representation of persisted rows instead of passing raw
dictionaries around.
​
Each model provides:
​
- :meth:`from_row` -- build an instance from a ``sqlite3.Row`` or ``dict``
  (as returned by :class:`~app.database.database.Database`).
- :meth:`to_dict`  -- convert back to a plain ``dict`` for persistence or
  serialization.
​
The models are deliberately compatible (by attribute name) with the
network-layer objects they originate from -- ``DiscoveredDevice`` and
``BandwidthSample`` -- so they can be used interchangeably with the database
write helpers.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

__all__ = [
    "AlertLevel",
    "Device",
    "DiscoveryRecord",
    "TrafficRecord",
    "Alert",
]


class AlertLevel(str, Enum):
    """Severity levels for alerts (string-valued for easy persistence)."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @classmethod
    def coerce(cls, value: Any) -> "AlertLevel":
        """Best-effort conversion of *value* to an :class:`AlertLevel`."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            return cls.INFO


def _get(source: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    """Read *key* from a mapping or an attribute-bearing object."""
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


@dataclass(slots=True)
class Device:
    """
    A device on the network (mirrors the ``devices`` table).

    Attributes
    ----------
    mac:
        MAC address (primary key, lower-case colon form).
    ip:
        Most recently seen IPv4 address.
    hostname:
        Reverse-DNS hostname, if known.
    vendor:
        Manufacturer resolved from the MAC OUI, if known.
    is_known:
        Whether the user has marked this device as known/trusted.
    online:
        Whether the device responded to the most recent scan.
    first_seen:
        POSIX timestamp of the first sighting.
    last_seen:
        POSIX timestamp of the most recent sighting.
    """

    mac: str
    ip: str | None = None
    hostname: str | None = None
    vendor: str | None = None
    is_known: bool = False
    online: bool = False
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | Any) -> "Device":
        """Build a :class:`Device` from a database row or device-like object."""
        now = time.time()
        return cls(
            mac=str(_get(row, "mac", "")).lower(),
            ip=_get(row, "ip"),
            hostname=_get(row, "hostname"),
            vendor=_get(row, "vendor"),
            is_known=bool(_get(row, "is_known", False)),
            online=bool(_get(row, "online", False)),
            first_seen=float(_get(row, "first_seen", now) or now),
            last_seen=float(_get(row, "last_seen", now) or now),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict with integer flags for SQLite."""
        data = asdict(self)
        data["is_known"] = int(self.is_known)
        data["online"] = int(self.online)
        return data

    @property
    def display_name(self) -> str:
        """Friendly label: hostname, else vendor, else IP, else MAC."""
        return self.hostname or self.vendor or self.ip or self.mac

    @property
    def last_seen_dt(self) -> datetime:
        """``last_seen`` as a local :class:`datetime`."""
        return datetime.fromtimestamp(self.last_seen)


@dataclass(slots=True)
class DiscoveryRecord:
    """
    A single scan sighting (mirrors the ``discovery_history`` table).
    """

    mac: str
    ip: str | None = None
    hostname: str | None = None
    vendor: str | None = None
    online: bool = True
    scanned_at: float = field(default_factory=time.time)
    id: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | Any) -> "DiscoveryRecord":
        """Build a :class:`DiscoveryRecord` from a database row."""
        now = time.time()
        return cls(
            id=_get(row, "id"),
            mac=str(_get(row, "mac", "")).lower(),
            ip=_get(row, "ip"),
            hostname=_get(row, "hostname"),
            vendor=_get(row, "vendor"),
            online=bool(_get(row, "online", True)),
            scanned_at=float(_get(row, "scanned_at", now) or now),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["online"] = int(self.online)
        return data


@dataclass(slots=True)
class TrafficRecord:
    """
    A bandwidth sample (mirrors the ``traffic_history`` table).

    Attribute names match :class:`~app.network.monitor.BandwidthSample` so the
    two are interchangeable when writing to the database.
    """

    timestamp: float
    download_mbps: float
    upload_mbps: float
    bytes_recv: int | None = None
    bytes_sent: int | None = None
    interface: str | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | Any) -> "TrafficRecord":
        """Build a :class:`TrafficRecord` from a database row or sample."""
        return cls(
            id=_get(row, "id"),
            timestamp=float(_get(row, "timestamp", 0.0) or 0.0),
            download_mbps=float(_get(row, "download_mbps", 0.0) or 0.0),
            upload_mbps=float(_get(row, "upload_mbps", 0.0) or 0.0),
            bytes_recv=_get(row, "bytes_recv"),
            bytes_sent=_get(row, "bytes_sent"),
            interface=_get(row, "interface"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def timestamp_dt(self) -> datetime:
        """``timestamp`` as a local :class:`datetime`."""
        return datetime.fromtimestamp(self.timestamp)


@dataclass(slots=True)
class Alert:
    """
    A user-facing alert/notification (mirrors the ``alerts`` table).
    """

    message: str
    level: AlertLevel = AlertLevel.INFO
    category: str | None = None
    created_at: float = field(default_factory=time.time)
    acknowledged: bool = False
    id: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | Any) -> "Alert":
        """Build an :class:`Alert` from a database row."""
        now = time.time()
        return cls(
            id=_get(row, "id"),
            message=str(_get(row, "message", "")),
            level=AlertLevel.coerce(_get(row, "level", AlertLevel.INFO)),
            category=_get(row, "category"),
            created_at=float(_get(row, "created_at", now) or now),
            acknowledged=bool(_get(row, "acknowledged", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["level"] = self.level.value
        data["acknowledged"] = int(self.acknowledged)
        return data

    @property
    def created_at_dt(self) -> datetime:
        """``created_at`` as a local :class:`datetime`."""
        return datetime.fromtimestamp(self.created_at)
