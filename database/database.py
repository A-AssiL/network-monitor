"""SQLite persistence layer.
​
Owns the application's database connection and all read/write access to it.
The schema covers the stores required by the spec plus packet capture:
​
- **devices**           -- the current known/seen devices (one row per MAC).
- **discovery_history** -- an append-only log of every scan sighting.
- **traffic_history**   -- periodic bandwidth samples for the graphs/history.
- **alerts**            -- notifications such as unknown-device detections.
- **packets**           -- captured packets (schema v2; feeds the capture view).
​
Design
------
- **Thread-safe.** Database writes are expected to happen on background
  workers (QThread/asyncio), never on the GUI thread. A single connection is
  opened with ``check_same_thread=False`` and guarded by a re-entrant lock,
  so callers on any thread can use the same :class:`Database` instance safely.
- **WAL mode** is enabled for better read/write concurrency.
- **Schema versioning.** A lightweight ``PRAGMA user_version`` check creates
  the schema on first run and applies ordered migration steps for existing
  databases, leaving room for future changes without a rewrite (supporting the
  project's "extend without refactoring" goal).
- **Decoupled from the network layer.** Write helpers accept either the
  network-layer objects (``DiscoveredDevice``, ``BandwidthSample``,
  ``CapturedPacket``) via duck typing, or plain values, so this module has no
  hard import of those types.
​
Typical usage
-------------
    >>> db = Database("network_monitor.db")
    >>> db.upsert_device(device)          # device from ArpScanner
    >>> db.record_traffic(sample)         # sample from BandwidthMonitor
    >>> db.record_packet(pkt)             # packet from CaptureService
    >>> rows = db.get_devices()
    >>> db.close()
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Current schema version. Bump when adding a migration step.
#   v1 -> devices, discovery_history, traffic_history, alerts
#   v2 -> + packets (packet capture)
SCHEMA_VERSION = 2

# Baseline schema. Every statement is idempotent (IF NOT EXISTS), so running
# the full set is always safe -- fresh databases get everything, and existing
# ones simply gain any newly added tables/indexes.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS devices (
        mac         TEXT PRIMARY KEY,
        ip          TEXT,
        hostname    TEXT,
        vendor      TEXT,
        is_known    INTEGER NOT NULL DEFAULT 0,
        online      INTEGER NOT NULL DEFAULT 0,
        first_seen  REAL NOT NULL,
        last_seen   REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discovery_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        mac         TEXT NOT NULL,
        ip          TEXT,
        hostname    TEXT,
        vendor      TEXT,
        online      INTEGER NOT NULL DEFAULT 1,
        scanned_at  REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS traffic_history (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp     REAL NOT NULL,
        download_mbps REAL NOT NULL,
        upload_mbps   REAL NOT NULL,
        bytes_recv    INTEGER,
        bytes_sent    INTEGER,
        interface     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        level        TEXT NOT NULL DEFAULT 'info',
        category     TEXT,
        message      TEXT NOT NULL,
        created_at   REAL NOT NULL,
        acknowledged INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS packets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp   REAL NOT NULL,
        source      TEXT,
        destination TEXT,
        protocol    TEXT,
        length      INTEGER,
        info        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_discovery_scanned_at ON discovery_history(scanned_at)",
    "CREATE INDEX IF NOT EXISTS idx_traffic_timestamp ON traffic_history(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_packets_timestamp ON packets(timestamp)",
)

# Ordered, per-version migration steps for changes that are NOT expressible as
# idempotent CREATE ... IF NOT EXISTS statements (e.g. ALTER TABLE, backfills).
# Additive tables/indexes belong in _SCHEMA_STATEMENTS above; this map is the
# home for future non-idempotent upgrades. Keys are the target version.
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    # 2: ("ALTER TABLE ...",),  # example for the future
}


class Database:
    """Thread-safe SQLite wrapper for Network Monitor Pro.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database file. Parent directories are
        created automatically. Use ``":memory:"`` for an ephemeral database
        (useful in tests).
    """

    def __init__(self, db_path: str | Path = "network_monitor.db") -> None:
        self._db_path = str(db_path)
        self._lock = threading.RLock()
        if self._db_path != ":memory:":
            Path(self._db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self._db_path = str(Path(self._db_path).expanduser())
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()
        logger.info("Database ready at %s", self._db_path)

    # -- setup -----------------------------------------------------------
    def _configure(self) -> None:
        """Apply connection-level PRAGMAs for durability and concurrency."""
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")

    def _migrate(self) -> None:
        """Create or upgrade the schema based on ``PRAGMA user_version``."""
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version >= SCHEMA_VERSION:
                return
            logger.info(
                "Migrating schema (from v%s to v%s)", version, SCHEMA_VERSION
            )
            # 1) Ensure all current tables/indexes exist (idempotent baseline).
            for statement in _SCHEMA_STATEMENTS:
                self._conn.execute(statement)
            # 2) Apply any ordered, non-idempotent steps newer than our version.
            for step in range(version + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS.get(step, ()):
                    self._conn.execute(statement)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()

    # -- devices ---------------------------------------------------------
    def upsert_device(self, device: Any) -> None:
        """Insert or update a device row (keyed by MAC).

        Preserves the original ``first_seen`` while refreshing ``last_seen``
        and all mutable fields. Accepts any object exposing ``mac``, ``ip``,
        ``hostname``, ``vendor``, ``online`` and (optionally) ``last_seen``.
        """
        with self._lock:
            if self._upsert_device_stmt(device):
                self._conn.commit()

    def _upsert_device_stmt(self, device: Any) -> bool:
        """Execute the device upsert WITHOUT committing. Returns True if run.

        Callers hold the lock and are responsible for committing, so this can
        be composed into a larger transaction (see :meth:`record_scan`).
        """
        mac = str(getattr(device, "mac", "")).lower()
        if not mac:
            logger.debug("Skipping device with no MAC: %r", device)
            return False
        ip = getattr(device, "ip", None)
        hostname = getattr(device, "hostname", None)
        vendor = getattr(device, "vendor", None)
        online = 1 if getattr(device, "online", False) else 0
        now = float(getattr(device, "last_seen", None) or time.time())
        self._conn.execute(
            """
            INSERT INTO devices
                (mac, ip, hostname, vendor, is_known, online, first_seen, last_seen)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(mac) DO UPDATE SET
                ip        = excluded.ip,
                hostname  = COALESCE(excluded.hostname, devices.hostname),
                vendor    = COALESCE(excluded.vendor, devices.vendor),
                online    = excluded.online,
                last_seen = excluded.last_seen
            """,
            (mac, ip, hostname, vendor, online, now, now),
        )
        return True

    def mark_known(self, mac: str, known: bool = True) -> None:
        """Flag a device as known/trusted (or not)."""
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET is_known=? WHERE mac=?",
                (1 if known else 0, mac.lower()),
            )
            self._conn.commit()

    def set_all_offline(self) -> None:
        """Mark every device offline (e.g. before applying a fresh scan)."""
        with self._lock:
            self._conn.execute("UPDATE devices SET online=0")
            self._conn.commit()

    def get_devices(self, only_online: bool = False) -> list[dict[str, Any]]:
        """Return all known devices as dictionaries, newest sighting first."""
        query = "SELECT * FROM devices"
        if only_online:
            query += " WHERE online=1"
        query += " ORDER BY last_seen DESC"
        return self._fetch_all(query)

    def get_device(self, mac: str) -> dict[str, Any] | None:
        """Return a single device by MAC, or ``None`` if not found."""
        rows = self._fetch_all("SELECT * FROM devices WHERE mac=?", (mac.lower(),))
        return rows[0] if rows else None

    def get_device_counts(self) -> dict[str, int]:
        """Return {total, online, known, unknown} counts for the Dashboard."""
        rows = self._fetch_all(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(online), 0) AS online,
                   COALESCE(SUM(is_known), 0) AS known
            FROM devices
            """
        )
        row = rows[0] if rows else {}
        total = int(row.get("total", 0) or 0)
        known = int(row.get("known", 0) or 0)
        return {
            "total": total,
            "online": int(row.get("online", 0) or 0),
            "known": known,
            "unknown": max(0, total - known),
        }

    # -- discovery history ----------------------------------------------
    def record_discovery(self, device: Any, scanned_at: float | None = None) -> None:
        """Append a discovery-history entry for a sighted device."""
        with self._lock:
            if self._record_discovery_stmt(device, scanned_at):
                self._conn.commit()

    def _record_discovery_stmt(self, device: Any, scanned_at: float | None = None) -> bool:
        """Execute the discovery insert WITHOUT committing. Returns True if run."""
        mac = str(getattr(device, "mac", "")).lower()
        if not mac:
            return False
        ts = float(scanned_at if scanned_at is not None else time.time())
        self._conn.execute(
            """
            INSERT INTO discovery_history
                (mac, ip, hostname, vendor, online, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mac,
                getattr(device, "ip", None),
                getattr(device, "hostname", None),
                getattr(device, "vendor", None),
                1 if getattr(device, "online", True) else 0,
                ts,
            ),
        )
        return True

    def record_scan(self, devices: list, scanned_at: float | None = None) -> None:
        """Persist a full scan: upsert each device and log a discovery entry.

        Runs as a single transaction so a scan is stored atomically -- either
        all rows land or none do.
        """
        ts = float(scanned_at if scanned_at is not None else time.time())
        with self._lock:
            for device in devices:
                self._upsert_device_stmt(device)
                self._record_discovery_stmt(device, ts)
            self._conn.commit()

    def get_discovery_history(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return the most recent discovery-history entries."""
        return self._fetch_all(
            "SELECT * FROM discovery_history ORDER BY scanned_at DESC LIMIT ?",
            (int(limit),),
        )

    def get_device_discovery(self, mac: str, limit: int = 200) -> list[dict[str, Any]]:
        """Return discovery history for one device (History page drill-down)."""
        return self._fetch_all(
            "SELECT * FROM discovery_history WHERE mac=? ORDER BY scanned_at DESC LIMIT ?",
            (mac.lower(), int(limit)),
        )

    def prune_discovery_history(self, max_rows: int = 100_000) -> None:
        """Delete the oldest discovery rows beyond *max_rows* to cap growth."""
        with self._lock:
            self._conn.execute(
                """
                DELETE FROM discovery_history
                WHERE id NOT IN (
                    SELECT id FROM discovery_history ORDER BY scanned_at DESC LIMIT ?
                )
                """,
                (int(max_rows),),
            )
            self._conn.commit()

    # -- traffic history ------------------------------------------------
    def record_traffic(self, sample: Any) -> None:
        """Append a bandwidth sample to the traffic history."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO traffic_history
                    (timestamp, download_mbps, upload_mbps, bytes_recv, bytes_sent, interface)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    float(getattr(sample, "timestamp", None) or time.time()),
                    float(getattr(sample, "download_mbps", 0.0)),
                    float(getattr(sample, "upload_mbps", 0.0)),
                    getattr(sample, "bytes_recv", None),
                    getattr(sample, "bytes_sent", None),
                    getattr(sample, "interface", None),
                ),
            )
            self._conn.commit()

    def get_traffic_history(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Return recent traffic samples in chronological order (oldest first)."""
        rows = self._fetch_all(
            "SELECT * FROM traffic_history ORDER BY timestamp DESC LIMIT ?",
            (int(limit),),
        )
        return list(reversed(rows))

    def get_traffic_since(self, seconds: float, limit: int = 5000) -> list[dict[str, Any]]:
        """Return traffic samples from the last *seconds*, oldest first."""
        cutoff = time.time() - float(seconds)
        rows = self._fetch_all(
            "SELECT * FROM traffic_history WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            (cutoff, int(limit)),
        )
        return list(reversed(rows))

    def get_traffic_stats(self, since: float | None = None) -> dict[str, Any]:
        """Return aggregate traffic stats, optionally since an epoch timestamp.

        Keys: ``samples``, ``avg_download``, ``max_download``, ``avg_upload``,
        ``max_upload`` (rates in Mbps). Averages/maxima are ``0.0`` when there
        are no matching samples.
        """
        where = ""
        params: tuple[Any, ...] = ()
        if since is not None:
            where = "WHERE timestamp >= ?"
            params = (float(since),)
        rows = self._fetch_all(
            f"""
            SELECT COUNT(*)                       AS samples,
                   COALESCE(AVG(download_mbps), 0) AS avg_download,
                   COALESCE(MAX(download_mbps), 0) AS max_download,
                   COALESCE(AVG(upload_mbps), 0)   AS avg_upload,
                   COALESCE(MAX(upload_mbps), 0)   AS max_upload
            FROM traffic_history {where}
            """,
            params,
        )
        return rows[0] if rows else {
            "samples": 0,
            "avg_download": 0.0,
            "max_download": 0.0,
            "avg_upload": 0.0,
            "max_upload": 0.0,
        }

    def prune_traffic_history(self, max_rows: int = 50_000) -> None:
        """Delete the oldest traffic rows beyond *max_rows* to cap growth."""
        with self._lock:
            self._conn.execute(
                """
                DELETE FROM traffic_history
                WHERE id NOT IN (
                    SELECT id FROM traffic_history ORDER BY timestamp DESC LIMIT ?
                )
                """,
                (int(max_rows),),
            )
            self._conn.commit()

    # -- packets (schema v2) --------------------------------------------
    def record_packet(self, packet: Any) -> None:
        """Append a single captured packet.

        Accepts any object exposing ``timestamp``, ``source``, ``destination``,
        ``protocol``, ``length`` and ``info`` (e.g. ``CapturedPacket``). The
        heavier ``detail``/``hexdump`` fields are intentionally not stored.
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO packets
                    (timestamp, source, destination, protocol, length, info)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                self._packet_row(packet),
            )
            self._conn.commit()

    def record_packets(self, packets: list) -> None:
        """Append many captured packets in one transaction (bulk write)."""
        rows = [self._packet_row(p) for p in packets]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO packets
                    (timestamp, source, destination, protocol, length, info)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()

    @staticmethod
    def _packet_row(packet: Any) -> tuple[Any, ...]:
        """Build a packets-table row tuple from a duck-typed packet object."""
        try:
            length = int(getattr(packet, "length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        return (
            float(getattr(packet, "timestamp", None) or time.time()),
            getattr(packet, "source", None),
            getattr(packet, "destination", None),
            getattr(packet, "protocol", None),
            length,
            getattr(packet, "info", None),
        )

    def get_packets(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Return recent captured packets in chronological order (oldest first)."""
        rows = self._fetch_all(
            "SELECT * FROM packets ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        return list(reversed(rows))

    def prune_packets(self, max_rows: int = 100_000) -> None:
        """Delete the oldest packet rows beyond *max_rows* to cap growth."""
        with self._lock:
            self._conn.execute(
                """
                DELETE FROM packets
                WHERE id NOT IN (
                    SELECT id FROM packets ORDER BY id DESC LIMIT ?
                )
                """,
                (int(max_rows),),
            )
            self._conn.commit()

    # -- alerts ----------------------------------------------------------
    def add_alert(
        self,
        message: str,
        level: str = "info",
        category: str | None = None,
    ) -> int:
        """Insert an alert and return its row id."""
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO alerts (level, category, message, created_at, acknowledged)
                VALUES (?, ?, ?, ?, 0)
                """,
                (level, category, message, time.time()),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def acknowledge_alert(self, alert_id: int) -> None:
        """Mark an alert as acknowledged."""
        with self._lock:
            self._conn.execute(
                "UPDATE alerts SET acknowledged=1 WHERE id=?", (int(alert_id),)
            )
            self._conn.commit()

    def get_alerts(self, unacknowledged_only: bool = False) -> list[dict[str, Any]]:
        """Return alerts, newest first."""
        query = "SELECT * FROM alerts"
        if unacknowledged_only:
            query += " WHERE acknowledged=0"
        query += " ORDER BY created_at DESC"
        return self._fetch_all(query)

    def get_unacknowledged_alert_count(self) -> int:
        """Return the number of unacknowledged alerts (Dashboard badge)."""
        rows = self._fetch_all(
            "SELECT COUNT(*) AS n FROM alerts WHERE acknowledged=0"
        )
        return int(rows[0]["n"]) if rows else 0

    # -- lifecycle / helpers --------------------------------------------
    def _fetch_all(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        """Run a SELECT and return rows as plain dictionaries."""
        with self._lock:
            cursor = self._conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Escape hatch for ad-hoc queries (thread-safe, auto-committed)."""
        with self._lock:
            cursor = self._conn.execute(query, params)
            self._conn.commit()
            return cursor

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error as exc:
                logger.debug("Error closing database: %s", exc)
        logger.info("Database connection closed")

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["Database", "SCHEMA_VERSION"]