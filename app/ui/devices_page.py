"""Devices page.

Displays every device discovered on the local network in a sortable,
searchable table and lets the user trigger a fresh ARP scan without freezing
the GUI.

Columns
-------
Hostname | IP Address | MAC Address | Vendor | Status | Last Seen

Features
--------
- **Sorting** on any column (IP and Last Seen sort by true value, not text).
- **Search** box that live-filters rows across all columns.
- **Refresh / Scan** button that runs the scan on a background thread
  (:class:`ScanWorker`) so the UI stays responsive.
- **Double-click** a row to open a details dialog.

Threading
---------
Scapy's ARP scan is blocking, so it runs inside a :class:`QThread`
(:class:`ScanWorker`). The worker owns an :class:`~app.network.scanner.ArpScanner`
and emits Qt signals for discovered devices, completion, and errors. The view
never performs network I/O on the GUI thread.
"""

from __future__ import annotations

import ipaddress
import logging
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

logger = logging.getLogger(__name__)

# Column layout for the devices table.
COL_HOSTNAME = 0
COL_IP = 1
COL_MAC = 2
COL_VENDOR = 3
COL_STATUS = 4
COL_LAST_SEEN = 5

HEADERS = ["Hostname", "IP Address", "MAC Address", "Vendor", "Status", "Last Seen"]

_ONLINE_COLOR = "#3fb950"
_OFFLINE_COLOR = "#8b949e"


class _SortableItem(QTableWidgetItem):
    """
    Table item that sorts by an explicit sort key rather than display text.

    Used so the IP column sorts numerically and Last Seen sorts by timestamp,
    while still showing human-friendly text.
    """

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


class ScanWorker(QThread):
    """
    Runs an ARP scan on a background thread.

    Signals
    -------
    device_found(object):
        Emitted for each discovered device as enrichment completes.
    finished_scan(list):
        Emitted with the full device list when the scan ends successfully.
    error(str):
        Emitted with a human-readable message if the scan fails.
    """

    device_found = Signal(object)
    finished_scan = Signal(list)
    error = Signal(str)

    def __init__(self, subnet: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._subnet = subnet
        self._scanner = None

    def run(self) -> None:  # noqa: D401 - QThread entry point
        """Perform the scan and emit results (executed on the worker thread)."""
        try:
            from app.network.scanner import ArpScanner, ScannerError
        except Exception as exc:
            self.error.emit(f"Scanner unavailable: {exc}")
            return

        try:
            self._scanner = ArpScanner()
            devices = self._scanner.scan(
                subnet=self._subnet,
                on_device=self.device_found.emit,
            )
            self.finished_scan.emit(devices)
        except ScannerError as exc:
            logger.warning("Scan failed: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:  # defensive: never crash the thread silently
            logger.exception("Unexpected scan error")
            self.error.emit(f"Unexpected error during scan: {exc}")

    def cancel(self) -> None:
        """Request cancellation of the running scan."""
        if self._scanner is not None:
            self._scanner.stop()


class DeviceDetailsDialog(QDialog):
    """Modal dialog showing the full details of a single device."""

    def __init__(self, device, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Device Details")
        self.setMinimumWidth(360)

        form = QFormLayout()
        form.addRow("Hostname:", QLabel(getattr(device, "hostname", None) or "\u2014"))
        form.addRow("IP Address:", QLabel(getattr(device, "ip", "\u2014")))
        form.addRow("MAC Address:", QLabel(getattr(device, "mac", "\u2014")))
        form.addRow("Vendor:", QLabel(getattr(device, "vendor", None) or "\u2014"))
        status = "Online" if getattr(device, "online", False) else "Offline"
        form.addRow("Status:", QLabel(status))
        form.addRow("Last Seen:", QLabel(_format_last_seen(getattr(device, "last_seen", None))))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)


class DevicesPage(QWidget):
    """
    Table view of discovered network devices with search and rescan.
​
    Signals
    -------
    device_activated(object):
        Emitted when a device row is double-clicked (also opens the details
        dialog); useful for other views that want to react.
    """

    device_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("devicesPage")

        # Maps IP -> device object so the details dialog can show full data
        # and repeat scans can update existing rows in place.
        self._devices: dict[str, object] = {}
        self._worker: ScanWorker | None = None

        self._search_box = QLineEdit()
        self._refresh_button = QPushButton("Scan")
        self._status_label = QLabel("")
        self._table = QTableWidget(0, len(HEADERS))

        self._build_ui()

    # -- construction ----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        heading = QLabel("Devices")
        heading.setStyleSheet("font-size: 22px; font-weight: 700; color: #e6edf3;")
        root.addWidget(heading)

        # Toolbar: search box + status + scan button.
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        self._search_box.setPlaceholderText("Search hostname, IP, MAC, or vendor\u2026")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._search_box, stretch=1)

        self._status_label.setStyleSheet("color: #8b949e;")
        toolbar.addWidget(self._status_label)

        self._refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_button.clicked.connect(self.start_scan)
        toolbar.addWidget(self._refresh_button)

        root.addLayout(toolbar)

        # Table.
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_row_double_clicked)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSortIndicator(COL_IP, Qt.SortOrder.AscendingOrder)

        root.addWidget(self._table, stretch=1)

    # -- scanning --------------------------------------------------------
    @Slot()
    def start_scan(self, subnet: str | None = None) -> None:
        """Kick off a background ARP scan, guarding against concurrent runs."""
        # A finished QThread may have had its underlying C++ object deleted
        # while the Python reference lingers. isValid() guards against
        # touching a dead object.
        if self._worker is not None and isValid(self._worker) and self._worker.isRunning():
            logger.debug("Scan already in progress; ignoring request")
            return
        self._worker = None  # clear any finished/stale worker before starting a new one

        self._set_scanning(True)

        self._worker = ScanWorker(subnet=subnet)
        self._worker.device_found.connect(self._on_device_found)
        self._worker.finished_scan.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_finished(self) -> None:
        """Drop the reference and schedule deletion once the thread finishes."""
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def _set_scanning(self, scanning: bool) -> None:
        """Toggle the busy state of the toolbar."""
        self._refresh_button.setEnabled(not scanning)
        self._refresh_button.setText("Scanning\u2026" if scanning else "Scan")
        if scanning:
            self._status_label.setText("Scanning network\u2026")

    @Slot(object)
    def _on_device_found(self, device) -> None:
        """Insert or update a single device row as it is discovered."""
        self.upsert_device(device)

    @Slot(list)
    def _on_scan_finished(self, devices: list) -> None:
        """Finalize the scan: refresh all rows and update the status line."""
        self.set_devices(devices)
        self._set_scanning(False)
        self._status_label.setText(
            f"{len(devices)} device(s) found \u00b7 "
            f"last scan {datetime.now().strftime('%H:%M:%S')}"
        )

    @Slot(str)
    def _on_scan_error(self, message: str) -> None:
        """Surface a scan error to the user."""
        self._set_scanning(False)
        self._status_label.setText("Scan failed")
        QMessageBox.warning(self, "Scan Error", message)

    # -- populating the table -------------------------------------------
    def set_devices(self, devices) -> None:
        """Replace the entire table contents with *devices*."""
        self._devices.clear()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for device in devices:
            self._append_row(device)
        self._table.setSortingEnabled(True)
        self._apply_filter(self._search_box.text())

    def upsert_device(self, device) -> None:
        """Add *device* or update its existing row (matched by IP)."""
        ip = getattr(device, "ip", None)
        if ip is None:
            return
        if ip in self._devices:
            self._update_row_for_ip(ip, device)
        else:
            self._append_row(device)
        self._apply_filter(self._search_box.text())

    def _append_row(self, device) -> None:
        """Append a new row for *device*."""
        ip = getattr(device, "ip", "")
        self._devices[ip] = device
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._populate_row(row, device)

    def _update_row_for_ip(self, ip: str, device) -> None:
        """Update the row whose IP matches, in place."""
        self._devices[ip] = device
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_IP)
            if item is not None and item.text() == ip:
                self._populate_row(row, device)
                return
        self._append_row(device)

    def _populate_row(self, row: int, device) -> None:
        """Fill every cell of *row* from *device*."""
        hostname = getattr(device, "hostname", None) or "\u2014"
        ip = getattr(device, "ip", "")
        mac = getattr(device, "mac", "")
        vendor = getattr(device, "vendor", None) or "\u2014"
        online = bool(getattr(device, "online", False))
        last_seen = getattr(device, "last_seen", None)

        self._table.setItem(row, COL_HOSTNAME, _SortableItem(hostname, hostname.lower()))
        self._table.setItem(row, COL_IP, _SortableItem(ip, _ip_sort_key(ip)))
        self._table.setItem(row, COL_MAC, _SortableItem(mac, mac.lower()))
        self._table.setItem(row, COL_VENDOR, _SortableItem(vendor, vendor.lower()))

        status_text = "Online" if online else "Offline"
        status_item = _SortableItem(status_text, 0 if online else 1)
        status_item.setForeground(Qt.GlobalColor.green if online else Qt.GlobalColor.gray)
        self._table.setItem(row, COL_STATUS, status_item)

        self._table.setItem(
            row,
            COL_LAST_SEEN,
            _SortableItem(_format_last_seen(last_seen), float(last_seen or 0.0)),
        )

    # -- interaction -----------------------------------------------------
    @Slot()
    def _apply_filter(self, text: str = "") -> None:
        """Hide rows that do not match the search term (case-insensitive)."""
        needle = (text or "").strip().lower()
        for row in range(self._table.rowCount()):
            if not needle:
                self._table.setRowHidden(row, False)
                continue
            match = False
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item is not None and needle in item.text().lower():
                    match = True
                    break
            self._table.setRowHidden(row, not match)

    def _on_row_double_clicked(self, index) -> None:
        """Open the details dialog for the double-clicked row."""
        row = index.row()
        ip_item = self._table.item(row, COL_IP)
        if ip_item is None:
            return
        device = self._devices.get(ip_item.text())
        if device is None:
            return
        self.device_activated.emit(device)
        DeviceDetailsDialog(device, self).exec()


def _ip_sort_key(ip: str):
    """Return a sortable key for an IP address (falls back to the string)."""
    try:
        return int(ipaddress.ip_address(ip))
    except ValueError:
        return ip


def _format_last_seen(last_seen) -> str:
    """Format a POSIX timestamp as a readable local time, or an em dash."""
    if not last_seen:
        return "\u2014"
    try:
        return datetime.fromtimestamp(float(last_seen)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, TypeError):
        return "\u2014"