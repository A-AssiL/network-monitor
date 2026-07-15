"""
Main application window.
​
The top-level shell of Network Monitor Pro. It composes the persistent chrome
around the swappable page content:
​
- **Left sidebar** navigation: Dashboard, Devices, Traffic, History, Settings.
- **Top toolbar**: global actions (Scan, and a theme toggle placeholder).
- **Main content area**: a :class:`QStackedWidget` that shows one page at a
  time, switched by the sidebar.
- **Status bar**: transient status messages.
​
The window applies the application-wide **dark theme** stylesheet and wires
the navigation to the individual page widgets. Pages that are not yet
implemented (e.g. History in Phase 1) fall back to a simple placeholder so the
navigation is always complete and the app always launches.
​
Architecture
------------
- The window owns the page instances but contains no network/database logic.
- Background workers created by the pages (e.g. the Devices scan worker) keep
  the GUI responsive; the window merely relays high-level actions such as the
  toolbar Scan button to the relevant page.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app import __app_name__, __version__
from app.ui.dashboard import DashboardPage
from app.ui.devices_page import DevicesPage
from app.ui.graphs_page import GraphsPage

# Settings page is optional at this stage; fall back gracefully if absent.
try:
    from app.ui.settings_page import SettingsPage
except Exception:  # not yet implemented
    SettingsPage = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

SIDEBAR_WIDTH = 200

# Navigation entries: (label, icon-emoji). Order defines sidebar + stack order.
_NAV_ITEMS: list[tuple[str, str]] = [
    ("Dashboard", "\U0001F4CA"),
    ("Devices", "\U0001F5A5"),
    ("Traffic", "\U0001F4C8"),
    ("History", "\U0001F553"),
    ("Settings", "\u2699"),
]

# Application-wide dark theme stylesheet.
_DARK_QSS = """
QMainWindow, QWidget { background-color: #16171d; color: #e6edf3;
    font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; }
QToolBar { background-color: #1b1c22; border: none; border-bottom: 1px solid #2c2e38;
    padding: 6px 10px; spacing: 8px; }
QToolBar QToolButton, QToolBar QPushButton { color: #e6edf3; background: #22242c;
    border: 1px solid #2c2e38; border-radius: 6px; padding: 6px 12px; }
QToolBar QToolButton:hover, QToolBar QPushButton:hover { background: #2c2f3a; }
QStatusBar { background-color: #1b1c22; color: #8b949e;
    border-top: 1px solid #2c2e38; }
QLineEdit, QComboBox { background: #1e1f26; border: 1px solid #2c2e38;
    border-radius: 6px; padding: 6px 8px; color: #e6edf3; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #4ea1ff; }
QPushButton { background: #238636; color: white; border: none; border-radius: 6px;
    padding: 8px 16px; font-weight: 600; }
QPushButton:hover { background: #2ea043; }
QPushButton:disabled { background: #30363d; color: #8b949e; }
QTableWidget { background: #1e1f26; alternate-background-color: #1a1b22;
    gridline-color: #2c2e38; border: 1px solid #2c2e38; border-radius: 8px;
    selection-background-color: #1f6feb; selection-color: white; }
QHeaderView::section { background: #22242c; color: #8b949e; padding: 8px;
    border: none; border-bottom: 1px solid #2c2e38; font-weight: 600; }
QScrollBar:vertical { background: #16171d; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #2c2e38; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""

# Sidebar-specific styling (applied to the sidebar container only).
_SIDEBAR_QSS = """
QWidget#sidebar { background-color: #101116; border-right: 1px solid #2c2e38; }
QPushButton#navButton { background: transparent; color: #8b949e; border: none;
    border-radius: 8px; padding: 10px 14px; text-align: left; font-size: 14px;
    font-weight: 500; }
QPushButton#navButton:hover { background: #1b1c22; color: #e6edf3; }
QPushButton#navButton:checked { background: #1f6feb22; color: #4ea1ff;
    font-weight: 700; }
"""


class _PlaceholderPage(QWidget):
    """Simple placeholder shown for pages not yet implemented."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 22px; font-weight: 700; color: #e6edf3;")
        note = QLabel("Coming soon.")
        note.setStyleSheet("color: #8b949e;")
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    """
    The application's main window: sidebar + toolbar + content + status bar.

    Parameters
    ----------
    config:
        Optional application configuration object/dict. Passed through to the
        Settings page when available; unused otherwise.
    """

    def __init__(self, config: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config

        self.setWindowTitle(f"{__app_name__}")
        self.resize(1180, 720)
        self.setStyleSheet(_DARK_QSS)

        self._stack = QStackedWidget()
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._pages: dict[str, QWidget] = {}

        self._build_pages()
        self._build_layout()
        self._build_toolbar()
        self._build_status_bar()

        # Start on the Dashboard.
        self._select_index(0)

    # -- construction ----------------------------------------------------

    def _build_pages(self) -> None:
        """Instantiate the page widgets (with placeholders where needed)."""
        self.dashboard_page = DashboardPage()
        self.devices_page = DevicesPage()
        self.graphs_page = GraphsPage()
        self.history_page = _PlaceholderPage("History")

        if SettingsPage is not None:
            try:
                self.settings_page: QWidget = SettingsPage(self._config)
            except Exception as exc:  # constructor signature mismatch, etc.
                logger.warning("Could not build SettingsPage: %s", exc)
                self.settings_page = _PlaceholderPage("Settings")
        else:
            self.settings_page = _PlaceholderPage("Settings")

        # Order must match _NAV_ITEMS.
        self._pages = {
            "Dashboard": self.dashboard_page,
            "Devices": self.devices_page,
            "Traffic": self.graphs_page,
            "History": self.history_page,
            "Settings": self.settings_page,
        }
        for label, _icon in _NAV_ITEMS:
            self._stack.addWidget(self._pages[label])

    def _build_layout(self) -> None:
        """Compose the sidebar and content area into the central widget."""
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())
        root.addWidget(self._stack, stretch=1)

        self.setCentralWidget(central)

    def _build_sidebar(self) -> QWidget:
        """Build the left navigation sidebar."""
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar.setStyleSheet(_SIDEBAR_QSS)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 20, 12, 20)
        layout.setSpacing(6)

        brand = QLabel(__app_name__)
        brand.setStyleSheet(
            "color: #e6edf3; font-size: 15px; font-weight: 800;"
            " padding: 4px 8px 16px 8px;"
        )
        layout.addWidget(brand)

        for index, (label, icon) in enumerate(_NAV_ITEMS):
            button = QPushButton(f"  {icon}   {label}")
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, i=index: self._select_index(i))
            self._nav_group.addButton(button, index)
            layout.addWidget(button)

        layout.addStretch(1)

        version = QLabel(f"v{__version__}")
        version.setStyleSheet("color: #565b66; padding: 4px 8px;")
        layout.addWidget(version)

        return sidebar

    def _build_toolbar(self) -> None:
        """Build the top toolbar with global actions."""
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._page_title = QLabel("Dashboard")
        self._page_title.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: #e6edf3; padding-left: 4px;"
        )
        toolbar.addWidget(self._page_title)

        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        toolbar.addWidget(spacer)

        self._scan_action = QAction("\U0001F50D  Scan", self)
        self._scan_action.setToolTip("Scan the local network for devices")
        self._scan_action.triggered.connect(self._on_scan_clicked)
        toolbar.addAction(self._scan_action)

    def _build_status_bar(self) -> None:
        """Initialize the bottom status bar."""
        self.statusBar().showMessage("Ready")

    # -- navigation ------------------------------------------------------

    def _select_index(self, index: int) -> None:
        """Switch to the page at *index* and sync the sidebar + toolbar."""
        if index < 0 or index >= self._stack.count():
            return
        self._stack.setCurrentIndex(index)
        label = _NAV_ITEMS[index][0]

        button = self._nav_group.button(index)
        if button is not None:
            button.setChecked(True)

        if hasattr(self, "_page_title"):
            self._page_title.setText(label)
        self.statusBar().showMessage(label)

    # -- actions ---------------------------------------------------------

    @Slot()
    def _on_scan_clicked(self) -> None:
        """Trigger a scan and jump to the Devices page to show progress."""
        # Navigate to Devices (index 1) so the user sees the results populate.
        self._select_index(1)
        self.statusBar().showMessage("Scanning network\u2026")
        self.devices_page.start_scan()

    # -- lifecycle -------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Ensure background workers are stopped cleanly on close."""
        worker = getattr(self.devices_page, "_worker", None)
        try:
            if worker is not None and worker.isRunning():
                worker.cancel()
                worker.wait(2000)
        except Exception as exc:  # never block shutdown
            logger.debug("Error stopping scan worker on close: %s", exc)
        super().closeEvent(event)
