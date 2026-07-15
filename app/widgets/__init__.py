"""
Reusable custom widgets.
​
Small, self-contained UI components (status indicators, metric cards, styled
tables, badges, etc.) that can be composed by the pages in :mod:`app.ui`.
​
This package is intentionally empty for now: Phase 1 keeps its widgets inline
within their pages (e.g. ``MetricCard`` in :mod:`app.ui.dashboard`). As those
components are reused across pages, extract them here and re-export them below
so callers can simply ``from app.widgets import MetricCard``.
​
Widgets in this package must remain view-only: no network or database access,
and no import from :mod:`app.network` or :mod:`app.database`.
"""

from __future__ import annotations

__all__: list[str] = []

# Phase 2+ convenience exports (uncomment as widgets are extracted here):
# from .metric_card import MetricCard
# from .status_badge import StatusBadge
