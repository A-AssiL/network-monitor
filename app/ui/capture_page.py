"""Packet Capture page.
​
A Wireshark-style live view over the packet capture engine. It shows captured
packets in a scrolling table, lets the user apply a BPF filter and Start/Stop
capturing, and renders a detail pane for the selected packet.
​
Architecture
------------
- Pure *view*. It never sniffs packets or touches the network itself. It emits
  :attr:`start_requested` / :attr:`stop_requested`, which the main window
  routes to :class:`~app.services.capture_service.CaptureService`. Captured
  packets flow back in through the :meth:`add_packets` (batch) / :meth:`add_packet`
  slots.
- Signals flow one way: the page requests, the service performs the work on a
  background thread and pushes results (or errors) back via slots.
- Packet objects are read via ``getattr`` (with fallback attribute names) so
  the view stays decoupled from the exact ``CapturedPacket`` dataclass.
- The table is bounded to :data:`_MAX_ROWS`; the oldest rows are dropped once
  the cap is reached so a long capture never grows memory without limit.
​
Performance
-----------
The capture service delivers packets in **batches** (a few times a second).
:meth:`add_packets` inserts an entire batch with widget repaints suspended and
trims/scrolls exactly once, so the table stays smooth even under a full
``filter=None`` capture. Columns use fixed widths (not ``ResizeToContents``,
which re-measures every row on each insert) for the same reason.
"""
from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Maximum packet rows retained in the table (oldest are dropped past this).
_MAX_ROWS: int = 10000

# -- Table columns -------------------------------------------------------
COL_NO = 0
COL_TIME = 1
COL_SOURCE = 2
COL_DEST = 3
COL_PROTO = 4
COL_LENGTH = 5
COL_INFO = 6
HEADERS = ["No.", "Time", "Source", "Destination", "Protocol", "Length", "Info"]

# Default column widths (Info is stretched to fill remaining space).
_COL_WIDTHS = {
    COL_NO: 70,
    COL_TIME: 120,
    COL_SOURCE: 180,
    COL_DEST: 180,
    COL_PROTO: 90,
    COL_LENGTH: 80,
}

_CARD_BG = "#1e1f26"
_CARD_BORDER = "#2c2e38"
_MUTED = "#8b949e"
_ERROR_COLOR = "#f85149"

# Per-protocol row accent colours (falls back to muted grey).
_PROTO_COLORS: dict[str, str] = {
    "TCP": "#4ea1ff",
    "UDP": "#3fb950",
    "ICMP": "#d29922",
    "ICMPV6": "#d29922",
    "ARP": "#a371f7",
    "DNS": "#56d4dd",
    "DHCP": "#e3b341",
    "HTTP": "#ff7b72",
    "TLS": "#ff7b72",
    "TCP/HTTP": "#ff7b72",
}


def _first_attr(obj: object, names: tuple[str, ...], default=None):
    """Return the first present/non-empty attribute among *names*."""
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return default


def _format_time(value) -> str:
    """Format a POSIX timestamp as HH:MM:SS.mmm, or an empty string."""
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%H:%M:%S.%f")[:-3]
    except (ValueError, OSError, TypeError):
        return str(value)


class CapturePage(QWidget):
    """Live packet-capture view (table + filter + Start/Stop + detail pane).

    Signals
    -------
    start_requested(object):
        Emitted with a config dict ``{"bpf_filter": str|None,
        "interface": str|None}`` when the user starts a capture.
    stop_requested():
        Emitted when the user stops the current capture.
    """

    start_requested = Signal(object)
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("capturePage")
        self._capturing = False
        self._available = True
        self._count = 0
        # Row-aligned packet objects (parallel to table rows; trimmed together).
        self._packets: list[object] = []

        self._filter_input = QLineEdit()
        self._interface_input = QLineEdit()
        self._toggle_button = QPushButton("Start Capture")
        self._clear_button = QPushButton("Clear")
        self._status_label = QLabel("Ready")
        self._table = QTableWidget(0, len(HEADERS))
        self._detail = QTextEdit()

        self._build_ui()

    # -- construction ----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # Header: title + status.
        header = QHBoxLayout()
        heading = QLabel("Packet Capture")
        heading.setStyleSheet("font-size: 22px; font-weight: 700; color: #e6edf3;")
        header.addWidget(heading)
        header.addStretch(1)
        self._status_label.setStyleSheet(f"color: {_MUTED};")
        header.addWidget(self._status_label)
        root.addLayout(header)

        # Controls row: filter + interface + start/stop + clear.
        controls = QHBoxLayout()
        controls.setSpacing(10)
        self._filter_input.setPlaceholderText("BPF filter, e.g. tcp port 80")
        controls.addWidget(self._filter_input, stretch=1)
        self._interface_input.setPlaceholderText("interface (blank = default)")
        self._interface_input.setMaximumWidth(220)
        controls.addWidget(self._interface_input)
        self._toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_button.clicked.connect(self._on_toggle_clicked)
        controls.addWidget(self._toggle_button)
        self._clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_button.clicked.connect(self.clear)
        controls.addWidget(self._clear_button)
        root.addLayout(controls)

        # Splitter: packet table (top) + detail pane (bottom).
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_table())
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

    def _build_table(self) -> QWidget:
        table = self._table
        table.setHorizontalHeaderLabels(HEADERS)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.itemSelectionChanged.connect(self._on_selection_changed)

        # Fixed/interactive widths avoid ResizeToContents re-measuring every
        # row on each insert (which bogs down high-rate captures). Info fills
        # the remaining width.
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_INFO, QHeaderView.ResizeMode.Stretch)
        for col, width in _COL_WIDTHS.items():
            table.setColumnWidth(col, width)
        return table

    def _build_detail(self) -> QWidget:
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select a packet to inspect its details.")
        self._detail.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {_CARD_BG};
                border: 1px solid {_CARD_BORDER};
                border-radius: 8px;
                color: #e6edf3;
                font-family: Consolas, 'DejaVu Sans Mono', monospace;
                font-size: 12px;
            }}
            """
        )
        return self._detail

    # -- slots (state in) ------------------------------------------------
    @Slot(object)
    def add_packets(self, packets: object) -> None:
        """Append a *batch* of captured packets efficiently.

        Repaints and sorting are suspended for the whole batch and the table
        is trimmed/scrolled exactly once, so a burst of hundreds of packets
        costs roughly one layout pass instead of one per packet.
        """
        if packets is None:
            return
        try:
            batch = list(packets)
        except TypeError:
            # Tolerate a single packet object being passed in.
            batch = [packets]
        if not batch:
            return

        table = self._table
        table.setUpdatesEnabled(False)
        was_sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        try:
            for packet in batch:
                self._append_row(packet)
            self._trim_rows()
        finally:
            table.setSortingEnabled(was_sorting)
            table.setUpdatesEnabled(True)
        table.scrollToBottom()

    @Slot(object)
    def add_packet(self, packet: object) -> None:
        """Append a single captured packet (dropping the oldest if full)."""
        self._append_row(packet)
        self._trim_rows()
        self._table.scrollToBottom()

    def _append_row(self, packet: object) -> None:
        """Insert one packet as the last row. No trim / scroll / repaint toggle.

        Callers are responsible for trimming and scrolling so a batch can do
        both once at the end.
        """
        table = self._table
        self._packets.append(packet)
        self._count += 1

        source = _first_attr(packet, ("source", "src"), "")
        dest = _first_attr(packet, ("destination", "dst"), "")
        protocol = str(_first_attr(packet, ("protocol", "proto"), "") or "")
        length = _first_attr(packet, ("length", "len"), "")
        info = _first_attr(packet, ("info", "summary"), "")
        ts = _first_attr(packet, ("timestamp", "time"), None)

        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, COL_NO, self._cell(str(self._count)))
        table.setItem(row, COL_TIME, self._cell(_format_time(ts)))
        table.setItem(row, COL_SOURCE, self._cell(str(source)))
        table.setItem(row, COL_DEST, self._cell(str(dest)))
        table.setItem(row, COL_PROTO, self._cell(protocol))
        table.setItem(row, COL_LENGTH, self._cell(str(length)))
        table.setItem(row, COL_INFO, self._cell(str(info)))

        # Colour the whole row by protocol for quick scanning.
        colour = _PROTO_COLORS.get(protocol.upper())
        if colour:
            brush = QBrush(QColor(colour))
            for col in range(len(HEADERS)):
                item = table.item(row, col)
                if item is not None:
                    item.setForeground(brush)

    def _trim_rows(self) -> None:
        """Drop the oldest rows/packets so the table never exceeds the cap."""
        table = self._table
        excess = table.rowCount() - _MAX_ROWS
        if excess <= 0:
            return
        for _ in range(excess):
            table.removeRow(0)
        del self._packets[:excess]

    @Slot(bool)
    def set_capturing(self, capturing: bool) -> None:
        """Reflect the capture engine's running state in the controls."""
        self._capturing = bool(capturing)
        self._toggle_button.setText("Stop Capture" if capturing else "Start Capture")
        self._filter_input.setEnabled(not capturing)
        self._interface_input.setEnabled(not capturing)
        if capturing:
            self._status_label.setStyleSheet(f"color: {_MUTED};")
            self._status_label.setText("Capturing\u2026")
        else:
            self._status_label.setStyleSheet(f"color: {_MUTED};")
            self._status_label.setText(f"Stopped \u00b7 {self._count} packet(s)")

    @Slot(str)
    def show_error(self, message: str) -> None:
        """Display a capture error (e.g. missing Npcap / insufficient privileges)."""
        self._status_label.setStyleSheet(f"color: {_ERROR_COLOR};")
        self._status_label.setText(message)
        logger.error("Capture error: %s", message)

    @Slot(bool)
    def set_available(self, available: bool) -> None:
        """Enable/disable the page when the capture backend is (un)available."""
        self._available = bool(available)
        self._toggle_button.setEnabled(available)
        if not available:
            self._status_label.setStyleSheet(f"color: {_ERROR_COLOR};")
            self._status_label.setText(
                "Packet capture unavailable (Scapy / Npcap not installed)."
            )

    @Slot()
    def clear(self) -> None:
        """Clear the table, detail pane, and packet counter."""
        self._table.setRowCount(0)
        self._packets.clear()
        self._count = 0
        self._detail.clear()
        if not self._capturing:
            self._status_label.setStyleSheet(f"color: {_MUTED};")
            self._status_label.setText("Ready")

    # -- interaction -----------------------------------------------------
    @Slot()
    def _on_toggle_clicked(self) -> None:
        """Request start/stop. The service confirms via ``set_capturing``."""
        if not self._available:
            return
        if self._capturing:
            self.stop_requested.emit()
        else:
            config = {
                "bpf_filter": self._filter_input.text().strip() or None,
                "interface": self._interface_input.text().strip() or None,
            }
            self.start_requested.emit(config)

    @Slot()
    def _on_selection_changed(self) -> None:
        """Render the selected packet's details in the detail pane."""
        row = self._table.currentRow()
        if 0 <= row < len(self._packets):
            self._detail.setPlainText(self._packet_detail(self._packets[row]))

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _cell(text: str) -> QTableWidgetItem:
        """Build a non-editable table cell."""
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def _packet_detail(packet: object) -> str:
        """Format a captured packet into a readable multi-line breakdown."""
        ts = _first_attr(packet, ("timestamp", "time"), None)
        rows = [
            ("Time", _format_time(ts)),
            ("Source", _first_attr(packet, ("source", "src"), "\u2014")),
            ("Destination", _first_attr(packet, ("destination", "dst"), "\u2014")),
            ("Protocol", _first_attr(packet, ("protocol", "proto"), "\u2014")),
            ("Length", _first_attr(packet, ("length", "len"), "\u2014")),
            ("Info", _first_attr(packet, ("info", "summary"), "\u2014")),
        ]
        lines = [f"{label:<13}: {value}" for label, value in rows]

        # Append any richer detail/hexdump the packet chooses to expose.
        detail = _first_attr(packet, ("detail", "description"), None)
        if detail:
            lines.append("")
            lines.append(str(detail))
        hexdump = _first_attr(packet, ("hexdump", "raw"), None)
        if hexdump:
            lines.append("")
            lines.append(str(hexdump))
        return "\n".join(lines)


__all__ = ["CapturePage"]
