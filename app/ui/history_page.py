"""History page.
​
A read-only browser for data that Network Monitor Pro has persisted to the
local SQLite database, split into two tabs:
​
- **Discovered Devices** -- every device ever seen on the network, with its
  last-known status and the time it was last observed.
- **Traffic History** -- persisted bandwidth samples (download/upload Mbps)
  recorded over time.
​
Architecture
------------
- This is a pure *view*. It performs **no** database or network access itself.
  Instead it exposes :meth:`set_devices` and :meth:`set_traffic_history`
  slots that a coordinating service (owned by the main window) calls with
  already-loaded domain objects.
- It emits :attr:`refresh_requested` when the user clicks *Refresh*; the main
  window connects that to the persistence layer and pushes fresh data back in.
- Objects are read via ``getattr`` so the view stays decoupled from the exact
  ``models.py`` dataclasses (mirrors the Devices/Dashboard pages).
"""
from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable
from datetime import datetime

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# -- Devices tab columns -------------------------------------------------
DEV_HOSTNAME = 0
DEV_IP = 1
DEV_MAC = 2
DEV_VENDOR = 3
DEV_STATUS = 4
DEV_LAST_SEEN = 5
DEVICE_HEADERS = [
    "Hostname",
    "IP Address",
    "MAC Address",
    "Vendor",
    "Status",
    "Last Seen",
]

# -- Traffic tab columns -------------------------------------------------
TRA_TIME = 0
TRA_DOWNLOAD = 1
TRA_UPLOAD = 2
TRA_INTERFACE = 3
TRAFFIC_HEADERS = ["Time", "Download (Mbps)", "Upload (Mbps)", "Interface"]

_ONLINE_COLOR = "#3fb950"
_OFFLINE_COLOR = "#8b949e"


class _SortableItem(QTableWidgetItem):
    """Table item that sorts by an explicit key instead of display text."""

    def __init__(self, text: str, sort_key: object) -> None:
        super().__init__(text)
        self._sort_key = sort_key
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def __lt__(self, other: QTableWidgetItem) -> bool:  # type: ignore[override]
        if isinstance(other, _SortableItem):
            try:
                return self._sort_key < other._sort_key  # type: ignore[operator]
            except TypeError:
                pass
        return super().__lt__(other)


class HistoryPage(QWidget):
    """Read-only view of persisted device discovery and traffic history.

    Signals
    -------
    refresh_requested():
        Emitted when the user asks to reload history from the database.
    device_activated(object):
        Emitted when a device row is double-clicked.
    """

    refresh_requested = Signal()
    device_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("historyPage")

        # IP -> device object, so a double-clicked row can surface full data.
        self._devices: dict[str, object] = {}
        self._last_devices_count = 0
        self._last_traffic_count = 0

        self._refresh_button = QPushButton("Refresh")
        self._status_label = QLabel("")
        self._device_table = QTableWidget(0, len(DEVICE_HEADERS))
        self._traffic_table = QTableWidget(0, len(TRAFFIC_HEADERS))

        self._build_ui()

    # -- construction ----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # Header: title + status + refresh button.
        header = QHBoxLayout()
        heading = QLabel("History")
        heading.setStyleSheet("font-size: 22px; font-weight: 700; color: #e6edf3;")
        header.addWidget(heading)
        header.addStretch(1)
        self._status_label.setStyleSheet("color: #8b949e;")
        header.addWidget(self._status_label)
        self._refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        header.addWidget(self._refresh_button)
        root.addLayout(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_device_tab(), "Discovered Devices")
        tabs.addTab(self._build_traffic_tab(), "Traffic History")
        root.addWidget(tabs, stretch=1)

    def _build_device_tab(self) -> QWidget:
        self._configure_table(self._device_table, DEVICE_HEADERS)
        self._device_table.doubleClicked.connect(self._on_device_double_clicked)
        header = self._device_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(DEV_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSortIndicator(DEV_LAST_SEEN, Qt.SortOrder.DescendingOrder)
        return self._device_table

    def _build_traffic_tab(self) -> QWidget:
        self._configure_table(self._traffic_table, TRAFFIC_HEADERS)
        header = self._traffic_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSortIndicator(TRA_TIME, Qt.SortOrder.DescendingOrder)
        return self._traffic_table

    @staticmethod
    def _configure_table(table: QTableWidget, headers: list[str]) -> None:
        table.setHorizontalHeaderLabels(headers)
        table.setSortingEnabled(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)

    # -- population ------------------------------------------------------
    @Slot(object)
    def set_devices(self, devices: Iterable) -> None:
        """Replace the Devices tab contents with *devices*."""
        self._devices.clear()
        table = self._device_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        count = 0
        for device in devices:
            self._append_device_row(device)
            count += 1
        table.setSortingEnabled(True)
        # Apply the default sort (newest last-seen first); setSortIndicator
        # alone only draws the arrow, it does not reorder rows.
        table.sortItems(DEV_LAST_SEEN, Qt.SortOrder.DescendingOrder)
        self._update_status(devices_count=count)

    @Slot(object)
    def set_traffic_history(self, records: Iterable) -> None:
        """Replace the Traffic tab contents with *records* (any order)."""
        table = self._traffic_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        count = 0
        for record in records:
            self._append_traffic_row(record)
            count += 1
        table.setSortingEnabled(True)
        table.sortItems(TRA_TIME, Qt.SortOrder.DescendingOrder)
        self._update_status(traffic_count=count)

    def _append_device_row(self, device: object) -> None:
        table = self._device_table
        ip = getattr(device, "ip", "") or ""
        self._devices[ip] = device

        hostname = getattr(device, "hostname", None) or "\u2014"
        mac = getattr(device, "mac", "") or ""
        vendor = getattr(device, "vendor", None) or "\u2014"
        online = bool(getattr(device, "online", False))
        last_seen = getattr(device, "last_seen", None)

        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, DEV_HOSTNAME, _SortableItem(hostname, hostname.lower()))
        table.setItem(row, DEV_IP, _SortableItem(ip, _ip_sort_key(ip)))
        table.setItem(row, DEV_MAC, _SortableItem(mac, mac.lower()))
        table.setItem(row, DEV_VENDOR, _SortableItem(vendor, vendor.lower()))

        status_text = "Online" if online else "Offline"
        status_item = _SortableItem(status_text, 0 if online else 1)
        status_item.setForeground(
            QBrush(QColor(_ONLINE_COLOR if online else _OFFLINE_COLOR))
        )
        table.setItem(row, DEV_STATUS, status_item)

        table.setItem(
            row,
            DEV_LAST_SEEN,
            _SortableItem(_format_ts(last_seen), float(last_seen or 0.0)),
        )

    def _append_traffic_row(self, record: object) -> None:
        table = self._traffic_table
        ts = getattr(record, "timestamp", None)
        download = float(getattr(record, "download_mbps", 0.0) or 0.0)
        upload = float(getattr(record, "upload_mbps", 0.0) or 0.0)
        interface = getattr(record, "interface", None) or "\u2014"

        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, TRA_TIME, _SortableItem(_format_ts(ts), float(ts or 0.0)))
        table.setItem(row, TRA_DOWNLOAD, _SortableItem(f"{download:.2f}", download))
        table.setItem(row, TRA_UPLOAD, _SortableItem(f"{upload:.2f}", upload))
        table.setItem(row, TRA_INTERFACE, _SortableItem(interface, interface.lower()))

    # -- interaction -----------------------------------------------------
    @Slot()
    def _on_refresh_clicked(self) -> None:
        """Ask the owner (main window) to reload history from the database."""
        self._status_label.setText("Refreshing\u2026")
        self.refresh_requested.emit()

    def _on_device_double_clicked(self, index) -> None:
        """Emit the device object for the double-clicked row."""
        row = index.row()
        ip_item = self._device_table.item(row, DEV_IP)
        if ip_item is None:
            return
        device = self._devices.get(ip_item.text())
        if device is not None:
            self.device_activated.emit(device)

    def _update_status(
        self,
        devices_count: int | None = None,
        traffic_count: int | None = None,
    ) -> None:
        """Refresh the status caption with the latest known counts."""
        if devices_count is not None:
            self._last_devices_count = devices_count
        if traffic_count is not None:
            self._last_traffic_count = traffic_count
        self._status_label.setText(
            f"{self._last_devices_count} device(s) \u00b7 "
            f"{self._last_traffic_count} traffic record(s) \u00b7 "
            f"updated {datetime.now().strftime('%H:%M:%S')}"
        )


def _ip_sort_key(ip: str):
    """Return a sortable key for an IP address (falls back to the string)."""
    try:
        return int(ipaddress.ip_address(ip))
    except ValueError:
        return ip


def _format_ts(value) -> str:
    """Format a POSIX timestamp as readable local time, or an em dash."""
    if not value:
        return "\u2014"
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, TypeError):
        return "\u2014"


__all__ = ["HistoryPage"]