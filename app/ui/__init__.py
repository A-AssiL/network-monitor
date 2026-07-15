"""
UI layer (PySide6 views).
​
Contains the main window and the individual pages shown in its content area:
dashboard, devices, traffic (graphs), and settings. Views stay free of
business logic and delegate work to the :mod:`app.network` and
:mod:`app.database` layers (via services), communicating through Qt signals
and slots.
​
Public API
----------
- :class:`~app.ui.main_window.MainWindow`
- :class:`~app.ui.dashboard.DashboardPage`
- :class:`~app.ui.devices_page.DevicesPage`
- :class:`~app.ui.graphs_page.GraphsPage`
- :class:`~app.ui.settings_page.SettingsPage`
​
Importing this package pulls in PySide6. Because the pages depend on it, that
is expected; keep non-GUI logic out of this layer so the rest of the app can
be imported and tested without a Qt runtime.
"""

from __future__ import annotations

from .dashboard import DashboardPage
from .devices_page import DevicesPage
from .graphs_page import GraphsPage
from .main_window import MainWindow
from .settings_page import SettingsPage

__all__ = [
    "MainWindow",
    "DashboardPage",
    "DevicesPage",
    "GraphsPage",
    "SettingsPage",
]
