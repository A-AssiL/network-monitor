"""
Settings page.
​
Lets the user view and change the application's runtime configuration:
​
- **Refresh interval** -- how often the bandwidth monitor / scanner sample
  (seconds).
- **Network interface** -- which NIC to monitor (or "All interfaces").
- **Theme** -- Dark or Light.
- **Database location** -- where the SQLite file is stored.
​
Architecture
------------
- This is a *view*: it reads initial values from a configuration object/dict
  and, on save, emits :attr:`SettingsPage.settings_changed` with a plain dict
  of the new values. The application layer is responsible for persisting the
  config (e.g. to ``config.json``) and applying side effects such as swapping
  the theme or restarting workers.
- Reading the available interfaces is delegated to the network layer's
  :class:`~app.network.monitor.BandwidthMonitor`, imported defensively so the
  page still loads if that module is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Sentinel label for monitoring every interface at once.
_ALL_INTERFACES = "All interfaces"

# Default values used when the config does not specify them.
_DEFAULTS: dict[str, Any] = {
    "refresh_interval": 1.0,
    "interface": None,
    "theme": "dark",
    "database_path": "network_monitor.db",
}


def _config_get(config: Any, key: str, default: Any) -> Any:
    """Read *key* from a dict-like or attribute-based config object."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class SettingsPage(QWidget):
    """
    Editable view of application settings.

    Parameters
    ----------
    config:
        The current configuration (a dict or an object with matching
        attributes). Values are read to seed the controls; the object is not
        mutated here.

    Signals
    -------
    settings_changed(dict):
        Emitted when the user saves, carrying the new settings as a dict with
        keys ``refresh_interval``, ``interface``, ``theme``, ``database_path``.
    """

    settings_changed = Signal(dict)

    def __init__(self, config: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._config = config

        self._interval_spin = QDoubleSpinBox()
        self._interface_combo = QComboBox()
        self._theme_combo = QComboBox()
        self._db_path_edit = QLineEdit()
        self._status_label = QLabel("")

        self._build_ui()
        self._load_from_config()

    # -- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        heading = QLabel("Settings")
        heading.setStyleSheet("font-size: 22px; font-weight: 700; color: #e6edf3;")
        root.addWidget(heading)

        card = QFrame()
        card.setObjectName("settingsCard")
        card.setStyleSheet(
            """
            QFrame#settingsCard {
                background-color: #1e1f26;
                border: 1px solid #2c2e38;
                border-radius: 10px;
            }
            QFrame#settingsCard QLabel { border: none; color: #c9d1d9; }
            """
        )
        form = QFormLayout(card)
        form.setContentsMargins(24, 24, 24, 24)
        form.setSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Refresh interval.
        self._interval_spin.setRange(0.5, 60.0)
        self._interval_spin.setSingleStep(0.5)
        self._interval_spin.setDecimals(1)
        self._interval_spin.setSuffix(" s")
        form.addRow("Refresh interval:", self._interval_spin)

        # Network interface.
        self._populate_interfaces()
        form.addRow("Network interface:", self._interface_combo)

        # Theme.
        self._theme_combo.addItems(["Dark", "Light"])
        form.addRow("Theme:", self._theme_combo)

        # Database location + browse button.
        db_row = QHBoxLayout()
        db_row.setSpacing(8)
        self._db_path_edit.setPlaceholderText("Path to SQLite database file")
        db_row.addWidget(self._db_path_edit, stretch=1)
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._on_browse_clicked)
        db_row.addWidget(browse)
        db_container = QWidget()
        db_container.setLayout(db_row)
        form.addRow("Database location:", db_container)

        root.addWidget(card)

        # Action row: status + save/reset.
        actions = QHBoxLayout()
        self._status_label.setStyleSheet("color: #8b949e;")
        actions.addWidget(self._status_label)
        actions.addStretch(1)

        reset_button = QPushButton("Reset")
        reset_button.setStyleSheet(
            "background: #30363d; color: #e6edf3;"
        )
        reset_button.clicked.connect(self._load_from_config)
        actions.addWidget(reset_button)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self._on_save_clicked)
        actions.addWidget(save_button)

        root.addLayout(actions)
        root.addStretch(1)

    def _populate_interfaces(self) -> None:
        """Fill the interface dropdown with the sentinel plus detected NICs."""
        self._interface_combo.clear()
        self._interface_combo.addItem(_ALL_INTERFACES)
        try:
            from app.network.monitor import BandwidthMonitor

            for name in BandwidthMonitor.available_interfaces():
                self._interface_combo.addItem(name)
        except Exception as exc:  # network layer unavailable
            logger.debug("Could not enumerate interfaces: %s", exc)

    # -- config load/save -----------------------------------------------

    @Slot()
    def _load_from_config(self) -> None:
        """Seed all controls from the current configuration."""
        interval = float(_config_get(self._config, "refresh_interval", _DEFAULTS["refresh_interval"]))
        interface = _config_get(self._config, "interface", _DEFAULTS["interface"])
        theme = str(_config_get(self._config, "theme", _DEFAULTS["theme"]))
        db_path = str(_config_get(self._config, "database_path", _DEFAULTS["database_path"]))

        self._interval_spin.setValue(interval)

        if not interface:
            self._interface_combo.setCurrentText(_ALL_INTERFACES)
        else:
            index = self._interface_combo.findText(interface)
            if index < 0:
                # Config references an interface we could not enumerate; add it.
                self._interface_combo.addItem(interface)
                index = self._interface_combo.findText(interface)
            self._interface_combo.setCurrentIndex(max(index, 0))

        self._theme_combo.setCurrentText("Light" if theme.lower() == "light" else "Dark")
        self._db_path_edit.setText(db_path)
        self._status_label.setText("")

    def _collect(self) -> dict[str, Any]:
        """Gather the current control values into a settings dict."""
        interface_text = self._interface_combo.currentText()
        interface = None if interface_text == _ALL_INTERFACES else interface_text
        return {
            "refresh_interval": round(self._interval_spin.value(), 1),
            "interface": interface,
            "theme": self._theme_combo.currentText().lower(),
            "database_path": self._db_path_edit.text().strip() or _DEFAULTS["database_path"],
        }

    @Slot()
    def _on_save_clicked(self) -> None:
        """Emit the new settings for the application layer to persist/apply."""
        settings = self._collect()
        logger.info("Settings saved: %s", settings)
        self.settings_changed.emit(settings)
        self._status_label.setText("Settings saved.")

    # -- interaction -----------------------------------------------------

    @Slot()
    def _on_browse_clicked(self) -> None:
        """Open a file dialog to choose the SQLite database location."""
        current = self._db_path_edit.text().strip() or _DEFAULTS["database_path"]
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Select database file",
            current,
            "SQLite database (*.db *.sqlite);;All files (*)",
        )
        if path:
            self._db_path_edit.setText(path)
