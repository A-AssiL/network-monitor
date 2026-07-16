"""Graphs / Traffic page.
​
A dedicated view for visualizing bandwidth over time. Where the Dashboard
shows a single compact live chart, this page provides fuller, real-time
traffic graphs:
​
- A combined **live bandwidth** chart (download + upload together).
- A separate **download history** chart.
- A separate **upload history** chart.
​
All three update in real time from the same sample stream and share a bounded
rolling history so long-running sessions never grow memory without limit.
​
Architecture
------------
- Pure *view*: state arrives through :meth:`GraphsPage.update_bandwidth`,
  which is connected to the bandwidth monitor worker's signal. No network or
  database access happens here.
- PyQtGraph is imported defensively; if it is unavailable the page shows an
  informative placeholder instead of crashing.
- A time-window selector lets the user cap how much history is shown.
"""
from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Maximum samples retained (upper bound across all window options).
MAX_HISTORY: int = 600  # e.g. 10 minutes at 1 sample/sec

# Selectable time windows (label -> number of samples shown).
_WINDOWS: dict[str, int] = {
    "1 min": 60,
    "5 min": 300,
    "10 min": 600,
}
_DEFAULT_WINDOW = "1 min"

_DOWNLOAD_COLOR = "#4ea1ff"
_UPLOAD_COLOR = "#ff7b72"
_CARD_BG = "#1e1f26"
_CARD_BORDER = "#2c2e38"


def _fill_brush(color: str, alpha: int = 40):
    """Return a translucent PyQtGraph brush for area fills under a curve."""
    import pyqtgraph as pg

    qcolor = QColor(color)
    qcolor.setAlpha(alpha)
    return pg.mkBrush(qcolor)


class GraphsPage(QWidget):
    """Real-time traffic graphs (combined live, download, and upload history)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("graphsPage")

        # Rolling history buffers shared by all charts.
        self._times: deque[float] = deque(maxlen=MAX_HISTORY)
        self._download: deque[float] = deque(maxlen=MAX_HISTORY)
        self._upload: deque[float] = deque(maxlen=MAX_HISTORY)
        self._sample_index: int = 0
        self._window: int = _WINDOWS[_DEFAULT_WINDOW]

        # Curve handles (None if PyQtGraph is unavailable).
        self._live_download_curve = None
        self._live_upload_curve = None
        self._download_curve = None
        self._upload_curve = None
        self._pg = None  # cached pyqtgraph module

        self._build_ui()

    # -- construction ----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # Header row: title + time-window selector.
        header = QHBoxLayout()
        heading = QLabel("Traffic")
        heading.setStyleSheet("font-size: 22px; font-weight: 700; color: #e6edf3;")
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(self._make_caption("Window:"))
        self._window_combo = QComboBox()
        self._window_combo.addItems(list(_WINDOWS.keys()))
        self._window_combo.setCurrentText(_DEFAULT_WINDOW)
        self._window_combo.currentTextChanged.connect(self._on_window_changed)
        header.addWidget(self._window_combo)
        root.addLayout(header)

        try:
            import pyqtgraph as pg

            pg.setConfigOptions(antialias=True)
            self._pg = pg

            # Combined live chart (larger, on top).
            live = self._new_plot("Live Bandwidth")
            self._live_download_curve = live.plot(
                pen=pg.mkPen(_DOWNLOAD_COLOR, width=2),
                name="Download",
                fillLevel=0,
                brush=_fill_brush(_DOWNLOAD_COLOR),
            )
            self._live_upload_curve = live.plot(
                pen=pg.mkPen(_UPLOAD_COLOR, width=2),
                name="Upload",
                fillLevel=0,
                brush=_fill_brush(_UPLOAD_COLOR),
            )
            root.addWidget(self._wrap_card("Live Bandwidth", live), stretch=2)

            # Download + upload history side by side.
            history_row = QHBoxLayout()
            history_row.setSpacing(16)

            dl = self._new_plot("Download")
            self._download_curve = dl.plot(
                pen=pg.mkPen(_DOWNLOAD_COLOR, width=2),
                fillLevel=0,
                brush=_fill_brush(_DOWNLOAD_COLOR),
            )
            history_row.addWidget(self._wrap_card("Download History", dl), stretch=1)

            ul = self._new_plot("Upload")
            self._upload_curve = ul.plot(
                pen=pg.mkPen(_UPLOAD_COLOR, width=2),
                fillLevel=0,
                brush=_fill_brush(_UPLOAD_COLOR),
            )
            history_row.addWidget(self._wrap_card("Upload History", ul), stretch=1)

            root.addLayout(history_row, stretch=3)
        except Exception as exc:  # pyqtgraph missing / backend failure
            logger.warning("PyQtGraph unavailable, graphs disabled: %s", exc)
            placeholder = QLabel("Traffic graphs unavailable (PyQtGraph not installed).")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #8b949e;")
            root.addWidget(placeholder, stretch=1)

    def _new_plot(self, y_label: str):
        """Create a styled PyQtGraph PlotWidget for the dark theme."""
        pg = self._pg
        plot = pg.PlotWidget()
        plot.setBackground(_CARD_BG)
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.setLabel("left", f"{y_label} (Mbps)")
        plot.setLabel("bottom", "Time", units="s")
        plot.addLegend(offset=(10, 10))
        plot.setMenuEnabled(False)
        plot.setMouseEnabled(x=False, y=True)
        return plot

    def _wrap_card(self, title: str, plot: QWidget) -> QFrame:
        """Wrap a plot in a titled, rounded card frame."""
        card = QFrame()
        card.setObjectName("graphCard")
        card.setStyleSheet(
            f"""
            QFrame#graphCard {{
                background-color: {_CARD_BG};
                border: 1px solid {_CARD_BORDER};
                border-radius: 10px;
            }}
            """
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        caption = QLabel(title)
        caption.setStyleSheet(
            "color: #e6edf3; font-size: 14px; font-weight: 600; border: none;"
        )
        layout.addWidget(caption)
        layout.addWidget(plot)
        return card

    @staticmethod
    def _make_caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #8b949e; margin-right: 6px;")
        return label

    # -- slots (state updates) ------------------------------------------
    @Slot(object)
    def update_bandwidth(self, sample) -> None:
        """Append a new bandwidth sample and refresh all charts in real time.

        Parameters
        ----------
        sample:
            A :class:`~app.network.monitor.BandwidthSample` (typed as
            ``object`` to keep the UI layer decoupled from the network layer).
        """
        download = float(getattr(sample, "download_mbps", 0.0))
        upload = float(getattr(sample, "upload_mbps", 0.0))
        self._times.append(float(self._sample_index))
        self._download.append(download)
        self._upload.append(upload)
        self._sample_index += 1
        self._redraw()

    def load_history(self, samples: Iterable) -> None:
        """Pre-populate the charts from persisted traffic history (oldest first)."""
        self._times.clear()
        self._download.clear()
        self._upload.clear()
        self._sample_index = 0
        for sample in samples:
            self._times.append(float(self._sample_index))
            self._download.append(float(getattr(sample, "download_mbps", 0.0)))
            self._upload.append(float(getattr(sample, "upload_mbps", 0.0)))
            self._sample_index += 1
        self._redraw()

    # -- internals -------------------------------------------------------
    @Slot(str)
    def _on_window_changed(self, label: str) -> None:
        """Change how many trailing samples are displayed."""
        self._window = _WINDOWS.get(label, self._window)
        self._redraw()

    def _redraw(self) -> None:
        """Push the trailing window of history to every curve."""
        if self._live_download_curve is None:
            return
        window = self._window
        times = list(self._times)[-window:]
        download = list(self._download)[-window:]
        upload = list(self._upload)[-window:]

        self._live_download_curve.setData(times, download)
        self._live_upload_curve.setData(times, upload)
        if self._download_curve is not None:
            self._download_curve.setData(times, download)
        if self._upload_curve is not None:
            self._upload_curve.setData(times, upload)


__all__ = ["GraphsPage"]