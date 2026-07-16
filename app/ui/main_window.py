"""Application main window.
​
The :class:`MainWindow` is the integration hub of Network Monitor Pro. It owns
the navigation shell (sidebar + toolbar + status bar), the feature pages, and
the background **services** that feed them:
​
- :class:`~app.services.monitor_service.MonitorService` -- live bandwidth samples.
- :class:`~app.services.scan_service.ScanService`       -- ARP scans + device persistence.
- :class:`~app.services.capture_service.CaptureService` -- packet capture -> packets_captured.
- :class:`~app.services.persistence_service.PersistenceService` -- traffic write-through and history read-back.
​
The window is created with an optional ``database`` (opened by ``main.py``). All
services and the persistence layer are optional: if a dependency is missing the
window still runs, it simply skips the corresponding wiring. Signals flow
strictly one way -- services emit domain objects, the window fans them out to
the relevant pages, and the pages remain pure views.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.ui.dashboard import DashboardPage
from app.ui.devices_page import DevicesPage
from app.ui.graphs_page import GraphsPage

# Optional pages / services -- soft-imported so a missing module never blocks
# application startup.
try:
    from app.ui.settings_page import SettingsPage
except Exception:  # pragma: no cover
    SettingsPage = None  # type: ignore[assignment]

try:
    from app.ui.history_page import HistoryPage
except Exception:  # pragma: no cover
    HistoryPage = None  # type: ignore[assignment]

try:
    from app.ui.capture_page import CapturePage
except Exception:  # pragma: no cover
    CapturePage = None  # type: ignore[assignment]

try:
    from services.monitor_service import MonitorService
except Exception:  # pragma: no cover
    try:
        from services.monitor_service import MonitorService
    except Exception:
        MonitorService = None  # type: ignore[assignment]

try:
    from services.scan_service import ScanService
except Exception:  # pragma: no cover
    try:
        from services.scan_service import ScanService
    except Exception:
        ScanService = None  # type: ignore[assignment]

try:
    from services.capture_service import CaptureService
except Exception:  # pragma: no cover
    try:
        from services.capture_service import CaptureService
    except Exception:
        CaptureService = None  # type: ignore[assignment]

try:
    from services.persistence_service import PersistenceService
except Exception:  # pragma: no cover
    try:
        from services.persistence_service import PersistenceService
    except Exception:
        PersistenceService = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Sidebar navigation entries, in order.
_NAV_ITEMS: tuple[str, ...] = (
    "Dashboard",
    "Devices",
    "Traffic",
    "Capture",
    "History",
    "Settings",
)

_DARK_QSS = """
QMainWindow, QWidget { background-color: #16171d; color: #e6edf3; }
QToolBar {
    background-color: #1b1c22;
    border-bottom: 1px solid #2c2e38;
    spacing: 8px;
    padding: 6px;
}
QStatusBar { background-color: #1b1c22; color: #8b949e; }
QListWidget#navSidebar {
    background-color: #1b1c22;
    border: none;
    border-right: 1px solid #2c2e38;
    outline: 0;
    padding-top: 8px;
}
QListWidget#navSidebar::item {
    padding: 12px 20px;
    color: #b6bec9;
    border: none;
}
QListWidget#navSidebar::item:selected {
    background-color: #262832;
    color: #ffffff;
    border-left: 3px solid #4ea1ff;
}
QListWidget#navSidebar::item:hover { background-color: #21232c; }
QPushButton {
    background-color: #262832;
    border: 1px solid #333644;
    border-radius: 6px;
    padding: 6px 14px;
    color: #e6edf3;
}
QPushButton:hover { background-color: #30333f; }
QPushButton:pressed { background-color: #3a3e4c; }
"""


class _PlaceholderPage(QWidget):
    """Simple centered-label placeholder for pages not yet available."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(f"{title}\n(coming soon)")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #8b949e; font-size: 16px;")
        layout.addWidget(label)


class MainWindow(QMainWindow):
    """Top-level window: navigation shell, feature pages, and services."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        database: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or {}
        self._database = database

        # Services (created in _start_services once the UI exists).
        self._monitor = None
        self._scan_service = None
        self._capture_service = None
        self._persistence = None
        self._packet_count = 0

        self.setWindowTitle("Network Monitor Pro")
        self.resize(1180, 720)
        self.setStyleSheet(_DARK_QSS)

        self._build_ui()
        self._start_services()

    # -- UI construction -------------------------------------------------
    def _build_ui(self) -> None:
        """Assemble the toolbar, sidebar, stacked pages, and status bar."""
        self._build_toolbar()

        central = QWidget(self)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar.
        self._sidebar = QListWidget()
        self._sidebar.setObjectName("navSidebar")
        self._sidebar.setFixedWidth(200)
        for name in _NAV_ITEMS:
            self._sidebar.addItem(QListWidgetItem(name))
        self._sidebar.currentRowChanged.connect(self._on_nav_changed)
        root.addWidget(self._sidebar)

        # Pages.
        self._stack = QStackedWidget()
        self._build_pages()
        root.addWidget(self._stack, 1)

        self.setCentralWidget(central)

        # Status bar.
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        self._sidebar.setCurrentRow(0)

    def _build_toolbar(self) -> None:
        """Create the top toolbar with the global Scan action."""
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        title = QLabel("  Network Monitor Pro  ")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        toolbar.addWidget(title)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self._scan_button = QPushButton("Scan Network")
        self._scan_button.clicked.connect(self._on_scan_clicked)
        toolbar.addWidget(self._scan_button)

    def _build_pages(self) -> None:
        """Instantiate the feature pages and register them in the stack."""
        self.dashboard_page = DashboardPage()
        self.devices_page = DevicesPage()
        self.graphs_page = GraphsPage()

        if CapturePage is not None:
            self.capture_page: QWidget = CapturePage()
        else:
            self.capture_page = _PlaceholderPage("Capture")

        if HistoryPage is not None:
            self.history_page: QWidget = HistoryPage()
        else:
            self.history_page = _PlaceholderPage("History")

        if SettingsPage is not None:
            self.settings_page: QWidget = SettingsPage(config=self._config)
        else:
            self.settings_page = _PlaceholderPage("Settings")

        # Order must match _NAV_ITEMS.
        self._pages = {
            "Dashboard": self.dashboard_page,
            "Devices": self.devices_page,
            "Traffic": self.graphs_page,
            "Capture": self.capture_page,
            "History": self.history_page,
            "Settings": self.settings_page,
        }
        for name in _NAV_ITEMS:
            self._stack.addWidget(self._pages[name])

    # -- services / wiring ----------------------------------------------
    def _start_services(self) -> None:
        """Create the background services and connect their signals to pages."""
        self._start_persistence()
        self._start_scan_service()
        self._start_monitor()
        self._start_capture()
        self._wire_settings()
        self._wire_history()

        # Populate the UI from any previously persisted data.
        if self._persistence is not None:
            self._persistence.load_all()

    def _start_persistence(self) -> None:
        """Create the traffic persistence service (write-through + read-back)."""
        if PersistenceService is None or self._database is None:
            logger.info("Persistence service unavailable; history will not be stored")
            return
        try:
            self._persistence = PersistenceService(self._database)
            self._persistence.traffic_history_loaded.connect(self._on_traffic_loaded)
            self._persistence.devices_loaded.connect(self._on_devices_loaded)
            self._persistence.error.connect(self._on_service_error)
            self._persistence.start()
            logger.info("Persistence service started")
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to start persistence service: %s", exc)
            self._persistence = None

    def _start_scan_service(self) -> None:
        """Create the scan service and route the Scan action through it."""
        if ScanService is None:
            logger.info("Scan service unavailable; using the devices page's local scan")
            return
        try:
            self._scan_service = ScanService(self._database)
            self._scan_service.scan_started.connect(self._on_scan_started)
            self._scan_service.device_found.connect(self._on_device_found)
            self._scan_service.finished_scan.connect(self._on_scan_finished)
            self._scan_service.error.connect(self._on_scan_error)
            self._redirect_devices_scan_button()
            logger.info("Scan service ready")
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to create scan service: %s", exc)
            self._scan_service = None

    def _redirect_devices_scan_button(self) -> None:
        """Point the devices page's inline Scan button at the scan service.

        Keeps a single persisting scan path without editing the devices page.
        All accesses are guarded so an API change there can't break startup.
        """
        button = getattr(self.devices_page, "_refresh_button", None)
        if button is None:
            return
        try:
            button.clicked.disconnect()
        except Exception:
            pass
        try:
            button.clicked.connect(self._start_scan)
        except Exception as exc:  # pragma: no cover
            logger.debug("Could not redirect devices scan button: %s", exc)

    def _start_monitor(self) -> None:
        """Start the live bandwidth monitor and fan samples out to consumers."""
        if MonitorService is None:
            logger.info("Monitor service unavailable; live bandwidth disabled")
            return
        try:
            interval = float(self._config_get("refresh_interval", 1.0) or 1.0)
        except (TypeError, ValueError):
            interval = 1.0
        interface = self._config_get("interface", None)
        try:
            self._monitor = MonitorService(interval=interval, interface=interface)
            self._monitor.sample_ready.connect(self._on_bandwidth_sample)
            self._monitor.error.connect(self._on_service_error)
            if self._persistence is not None:
                # Persist every sample (batched on the persistence thread).
                self._monitor.sample_ready.connect(self._persistence.record_sample)
            self._monitor.start()
            logger.info("Bandwidth monitor started (interval=%ss)", interval)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to start bandwidth monitor: %s", exc)
            self._monitor = None

    def _start_capture(self) -> None:
        """Create the packet capture service and wire it to the capture page.

        The page is a pure view: it *requests* start/stop and the service
        confirms state, so the connection is two-way but the data flow stays
        one-directional (service -> page for packets/state). Packets arrive in
        batches (see CaptureService) to keep the GUI responsive.
        """
        page = self.capture_page
        if CaptureService is None:
            logger.info("Capture service unavailable; packet capture disabled")
            self._set_capture_available(False)
            return
        try:
            self._capture_service = CaptureService(database=self._database)
            # service -> page / window
            self._capture_service.packets_captured.connect(self._on_packets_captured)
            self._capture_service.capture_started.connect(self._on_capture_started)
            self._capture_service.capture_stopped.connect(self._on_capture_stopped)
            self._capture_service.error.connect(self._on_capture_error)
            # page -> service (only if the real capture page is present)
            start_sig = getattr(page, "start_requested", None)
            stop_sig = getattr(page, "stop_requested", None)
            if start_sig is not None:
                start_sig.connect(self._capture_service.start)
            if stop_sig is not None:
                stop_sig.connect(self._capture_service.stop)
            available = self._capture_service.is_available()
            self._set_capture_available(available)
            logger.info("Capture service ready (available=%s)", available)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to create capture service: %s", exc)
            self._capture_service = None
            self._set_capture_available(False)

    def _wire_settings(self) -> None:
        """React to live settings changes (interface / interval)."""
        page = self.settings_page
        if hasattr(page, "settings_changed"):
            try:
                page.settings_changed.connect(self._on_settings_changed)
            except Exception as exc:  # pragma: no cover
                logger.debug("Could not wire settings_changed: %s", exc)

    def _wire_history(self) -> None:
        """Wire the History page's refresh request to a reload from the DB."""
        page = self.history_page
        if hasattr(page, "refresh_requested"):
            try:
                page.refresh_requested.connect(self._on_history_refresh)
            except Exception as exc:  # pragma: no cover
                logger.debug("Could not wire history refresh: %s", exc)

    # -- helpers ---------------------------------------------------------
    def _config_get(self, key: str, default: Any = None) -> Any:
        """Safe config accessor."""
        try:
            return self._config.get(key, default)
        except AttributeError:
            return default

    @staticmethod
    def _as_objects(rows: Any) -> list[Any]:
        """Wrap dict rows from the DB as attribute-accessible objects.

        The database returns plain dicts, but the pages read domain fields via
        ``getattr``; wrapping each row in a ``SimpleNamespace`` bridges the two
        without coupling the UI to the storage format.
        """
        result: list[Any] = []
        for row in rows or []:
            result.append(SimpleNamespace(**row) if isinstance(row, dict) else row)
        return result

    def _start_scan(self, *_args: object) -> None:
        """Trigger a network scan via the service, falling back to the page."""
        if self._scan_service is not None:
            self._scan_service.start_scan()
        elif hasattr(self.devices_page, "start_scan"):
            self.devices_page.start_scan()

    def _set_capture_available(self, available: bool) -> None:
        """Tell the capture page whether the backend is usable."""
        setter = getattr(self.capture_page, "set_available", None)
        if callable(setter):
            try:
                setter(available)
            except Exception:  # pragma: no cover
                pass

    # -- navigation ------------------------------------------------------
    @Slot(int)
    def _on_nav_changed(self, row: int) -> None:
        if 0 <= row < self._stack.count():
            self._stack.setCurrentIndex(row)

    @Slot()
    def _on_scan_clicked(self) -> None:
        """Toolbar Scan: jump to the Devices page and start a scan."""
        try:
            self._sidebar.setCurrentRow(_NAV_ITEMS.index("Devices"))
        except ValueError:
            pass
        self._start_scan()

    # -- bandwidth -------------------------------------------------------
    @Slot(object)
    def _on_bandwidth_sample(self, sample: object) -> None:
        """Fan a live bandwidth sample out to the dashboard and graphs."""
        for page in (self.dashboard_page, self.graphs_page):
            update = getattr(page, "update_bandwidth", None)
            if callable(update):
                update(sample)

    # -- scanning --------------------------------------------------------
    @Slot()
    def _on_scan_started(self) -> None:
        self._set_scan_busy(True)
        self.statusBar().showMessage("Scanning network\u2026")

    @Slot(object)
    def _on_device_found(self, device: object) -> None:
        upsert = getattr(self.devices_page, "upsert_device", None)
        if callable(upsert):
            upsert(device)

    @Slot(list)
    def _on_scan_finished(self, devices: list) -> None:
        set_devices = getattr(self.devices_page, "set_devices", None)
        if callable(set_devices):
            set_devices(devices)
        self._set_scan_busy(False)
        online = sum(1 for d in devices if getattr(d, "online", False))
        self._update_device_counts(online, len(devices))
        self.statusBar().showMessage(f"Scan complete: {len(devices)} device(s) found")
        # Reload persisted history/devices so the History tab stays current.
        if self._persistence is not None:
            self._persistence.load_all()

    @Slot(str)
    def _on_scan_error(self, message: str) -> None:
        self._set_scan_busy(False)
        self.statusBar().showMessage(f"Scan failed: {message}")
        logger.error("Scan failed: %s", message)

    def _set_scan_busy(self, busy: bool) -> None:
        """Reflect scan progress in the toolbar button and devices page."""
        self._scan_button.setEnabled(not busy)
        self._scan_button.setText("Scanning\u2026" if busy else "Scan Network")
        setter = getattr(self.devices_page, "_set_scanning", None)
        if callable(setter):
            try:
                setter(busy)
            except Exception:
                pass

    def _update_device_counts(self, connected: int, discovered: int) -> None:
        update = getattr(self.dashboard_page, "update_device_counts", None)
        if callable(update):
            update(connected, discovered)

    # -- packet capture --------------------------------------------------
    @Slot(object)
    def _on_packets_captured(self, packets: object) -> None:
        """Forward a *batch* of captured packets to the page and dashboard.

        Prefers the page's batch API (``add_packets``); falls back to calling
        ``add_packet`` per item so this works even before the capture page is
        upgraded. Either way the GUI is touched at most a few times a second.
        """
        try:
            count = len(packets)  # type: ignore[arg-type]
        except TypeError:
            # Defensive: tolerate a single packet if something emits one.
            packets = [packets]
            count = 1
        if not count:
            return

        add_many = getattr(self.capture_page, "add_packets", None)
        if callable(add_many):
            add_many(packets)
        else:
            add_one = getattr(self.capture_page, "add_packet", None)
            if callable(add_one):
                for packet in packets:
                    add_one(packet)

        self._packet_count += count
        update = getattr(self.dashboard_page, "update_capture_stats", None)
        if callable(update):
            update(self._packet_count)

    @Slot()
    def _on_capture_started(self) -> None:
        self._set_capturing(True)

    @Slot()
    def _on_capture_stopped(self) -> None:
        self._set_capturing(False)

    def _set_capturing(self, capturing: bool) -> None:
        setter = getattr(self.capture_page, "set_capturing", None)
        if callable(setter):
            try:
                setter(capturing)
            except Exception:  # pragma: no cover
                pass
        self.statusBar().showMessage(
            "Packet capture running\u2026" if capturing else "Packet capture stopped"
        )

    @Slot(str)
    def _on_capture_error(self, message: str) -> None:
        shower = getattr(self.capture_page, "show_error", None)
        if callable(shower):
            try:
                shower(message)
            except Exception:  # pragma: no cover
                pass
        self.statusBar().showMessage(f"Capture error: {message}")
        logger.warning("Capture error: %s", message)

    # -- persistence callbacks ------------------------------------------
    @Slot(object)
    def _on_traffic_loaded(self, rows: object) -> None:
        """Feed persisted traffic history to the dashboard, graphs and history."""
        samples = self._as_objects(rows)
        for page in (self.dashboard_page, self.graphs_page):
            loader = getattr(page, "load_history", None)
            if callable(loader):
                loader(samples)
        setter = getattr(self.history_page, "set_traffic_history", None)
        if callable(setter):
            setter(samples)

    @Slot(object)
    def _on_devices_loaded(self, rows: object) -> None:
        """Feed persisted devices to the History page and dashboard counts."""
        devices = self._as_objects(rows)
        setter = getattr(self.history_page, "set_devices", None)
        if callable(setter):
            setter(devices)
        online = sum(1 for d in devices if getattr(d, "online", False))
        self._update_device_counts(online, len(devices))

    @Slot()
    def _on_history_refresh(self) -> None:
        if self._persistence is not None:
            self._persistence.load_all()

    @Slot(str)
    def _on_service_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        logger.warning("Service error: %s", message)

    # -- settings --------------------------------------------------------
    @Slot(dict)
    def _on_settings_changed(self, settings: dict) -> None:
        """Apply interface/interval changes to the running monitor live."""
        if not isinstance(settings, dict) or self._monitor is None:
            return
        if "interface" in settings:
            setter = getattr(self._monitor, "set_interface", None)
            if callable(setter):
                setter(settings["interface"])
        if "refresh_interval" in settings:
            setter = getattr(self._monitor, "set_interval", None)
            if callable(setter):
                try:
                    setter(float(settings["refresh_interval"]))
                except (TypeError, ValueError):
                    pass

    # -- lifecycle -------------------------------------------------------
    def closeEvent(self, event: Any) -> None:
        """Stop all background services cleanly before the window closes."""
        for service in (
            self._monitor,
            self._scan_service,
            self._capture_service,
            self._persistence,
        ):
            stop = getattr(service, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:  # pragma: no cover
                    logger.debug("Error stopping %s: %s", service, exc)
        super().closeEvent(event)


__all__ = ["MainWindow"]
