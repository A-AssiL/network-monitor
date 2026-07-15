"""
Generic utilities.

Framework-agnostic helpers shared across the application, such as:

- logging configuration
- configuration loading/saving (``config.json``)
- value formatting (e.g. bytes/sec -> Mbps, timestamps -> human strings)

Design
------
- Utilities must stay **dependency-light and side-effect-free** on import.
- This package must **not** import from :mod:`app.ui`, :mod:`app.network`, or
  :mod:`app.database`; it sits at the bottom of the dependency graph so any
  layer can use it freely without creating import cycles.

This package is currently a placeholder. Phase 1 keeps small helpers inline
(e.g. logging/config setup in ``main.py``, formatting inside the pages); they
can be consolidated here and re-exported below as the codebase grows.
"""

from __future__ import annotations

__all__: list[str] = []

# Phase 2+ convenience exports (uncomment as helpers are extracted here):
# from .logging_setup import setup_logging
# from .config import Config, load_config, save_config
# from .formatting import format_mbps, format_timestamp