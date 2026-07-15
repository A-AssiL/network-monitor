"""
Network Monitor Pro
===================
​
A professional Windows desktop application for local network monitoring.
​
This top-level package ties together the UI, networking, persistence, and
service layers. It intentionally exposes only lightweight metadata so that
importing :mod:`app` never triggers heavy imports (PySide6, Scapy, etc.).
​
Layers
------
- ``app.ui``        : PySide6 views (windows and pages).
- ``app.widgets``   : Reusable custom widgets.
- ``app.network``   : Domain logic (scanning, monitoring, lookups).
- ``app.database``  : SQLite persistence and data models.
- ``app.services``  : Cross-cutting application services.
- ``app.utils``     : Generic helpers (logging, config, formatting).
- ``app.resources`` : Static resources (icons, stylesheets, OUI data).
"""

from __future__ import annotations

__app_name__: str = "Network Monitor Pro"
__version__: str = "0.1.0"
__author__: str = "ANOUNE ASSIL"

__all__ = ["__app_name__", "__version__", "__author__"]
