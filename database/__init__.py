"""
Persistence layer (SQLite).
​
Owns the database connection and the typed models for the four stores used by
Network Monitor Pro: known devices, discovery history, traffic history, and
alerts.
​
Public API
----------
- :class:`~app.database.database.Database` -- the thread-safe SQLite wrapper.
- :class:`~app.database.models.Device`
- :class:`~app.database.models.DiscoveryRecord`
- :class:`~app.database.models.TrafficRecord`
- :class:`~app.database.models.Alert` / :class:`~app.database.models.AlertLevel`

Usage
-----
    from app.database import Database, Device, Alert, AlertLevel

    db = Database("network_monitor.db")
    db.upsert_device(device)
    devices = [Device.from_row(row) for row in db.get_devices()]

The imports here only require the standard library (``sqlite3``), so importing
this package is always safe.
"""

from __future__ import annotations

from .database import SCHEMA_VERSION, Database
from .models import Alert, AlertLevel, Device, DiscoveryRecord, TrafficRecord

__all__ = [
    "Database",
    "SCHEMA_VERSION",
    "Device",
    "DiscoveryRecord",
    "TrafficRecord",
    "Alert",
    "AlertLevel",
]
