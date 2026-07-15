"""Dashboard page.
​
The landing view of Network Monitor Pro. It surfaces the most important
real-time metrics at a glance:
​
- Current **download** speed (Mbps)
- Current **upload** speed (Mbps)
- Number of **connected** devices (online right now)
- Total **discovered** devices (seen at any point)
- A **live bandwidth graph** plotting download/upload history
​
Architecture
------------
- This is a pure *view*: it renders state pushed to it via slots and never
  performs network I/O or database access itself. Background workers (the
  bandwidth monitor / scanner) emit signals that are connected to
  :meth:`DashboardPage.update_bandwidth` and
  :meth:`DashboardPage.update_device_counts`.
- The live graph keeps a fixed-length rolling history so memory stays bounded
  regardless of how long the app runs.
- PyQtGraph is imported lazily/defensively so the rest of the UI still loads
  if the optional plotting dependency is missing.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Number of samples retained in the live graph (e.g. 120s at 1 sample/sec).
HISTORY_LENGTH: int = 120

# Palette used for the metric cards and graph traces (dark theme).
_DOWNLOAD_COLOR = "#4ea1ff"
_UPLOAD_COLOR = "#ff7b72"
_CARD_BG = "#1e1f26"
_CARD_BORDER = "#2c2e38"
_ACCENT_DEVICES = "#3fb950"
_ACCENT_DISCOVERED = "#d29922"


class MetricCard(QFrame):
    """
    A compact card showing a single labelled metric.

    Parameters
    ----------
    title:
        Caption shown above the value (e.g. ``"Download"``).
    unit:
        Optional unit suffix rendered after the value (e.g. ``"Mbps"``).
    accent:
        Hex colour used for the value text.
    """

    def __init__(
        self,
        title: str,
        unit: str = "",
        accent: str = "#ffffff",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._unit = unit
        self.setObjectName("metricCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(96)
        self.setStyleSheet(
            f"""
            QFrame#metricCard {{
                background-color: {_CARD_BG};
                border: 1px solid {_CARD_BORDER};
                border-radius: 10px;
            }}
            """
        )

        self._title_label = QLabel(title.upper())
        self._title_label.setStyleSheet(
            "color: #8b949e; font-size: 11px; font-weight: 600;"
            " letter-spacing: 1px; border: none;"
        )

        self._value_label = QLabel("--")
        self._value_label.setStyleSheet(
            f"color: {accent}; font-size: 30px; font-weight: 700; border: none;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)
        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)
        layout.addStretch(1)

    def set_value(self, value: str | float | int) -> None:
        """Update the displayed value, appending the unit if configured."""
        if isinstance(value, float):
            text = f"{value:.2f}"
        else:
            text = str(value)
        if self._unit:
            text = f"{text} {self._unit}"
        self._value_label.setText(text)


class DashboardPage(QWidget):
    """
    Real-time overview page.

    Consumes :class:`~app.network.monitor.BandwidthSample` updates and device
    counts, displaying them as metric cards plus a live download/upload graph.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dashboardPage")

        # Rolling history buffers for the live graph.
        self._times: deque[float] = deque(maxlen=HISTORY_LENGTH)
        self._download: deque[float] = deque(maxlen=HISTORY_LENGTH)
        self._upload: deque[float] = deque(maxlen=HISTORY_LENGTH)
        self._sample_index: int = 0

        self._download_card = MetricCard("Download", "Mbps", _DOWNLOAD_COLOR)
        self._upload_card = MetricCard("Upload", "Mbps", _UPLOAD_COLOR)
        self._connected_card = MetricCard("Connected Devices", "", _ACCENT_DEVICES)
        self._discovered_card = MetricCard("Total Discovered", "", _ACCENT_DISCOVERED)

        self._plot_widget: QWidget | None = None
        self._download_curve = None
        self._upload_curve = None

        self._build_ui()

    # -- construction ----------------------------------------------------
    def _build_ui(self) -> None:
        """Assemble the metric cards and live graph."""
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        heading = QLabel("Dashboard")
        heading.setStyleSheet(
            "font-size: 22px; font-weight: 700; color: #e6edf3;"
        )
        root.addWidget(heading)

        # Metric cards laid out in a responsive 2x2 / 1x4 grid.
        cards = QGridLayout()
        cards.setSpacing(16)
        cards.addWidget(self._download_card, 0, 0)
        cards.addWidget(self._upload_card, 0, 1)
        cards.addWidget(self._connected_card, 0, 2)
        cards.addWidget(self._discovered_card, 0, 3)
        root.addLayout(cards)

        root.addWidget(self._build_graph(), stretch=1)

    def _build_graph(self) -> QWidget:
        """
        Build the live bandwidth graph, degrading gracefully if PyQtGraph is
        unavailable.
        """
        container = QFrame()
        container.setObjectName("graphCard")
        container.setStyleSheet(
            f"""
            QFrame#graphCard {{
                background-color: {_CARD_BG};
                border: 1px solid {_CARD_BORDER};
                border-radius: 10px;
            }}
            """
        )
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Live Bandwidth")
        title.setStyleSheet(
            "color: #e6edf3; font-size: 14px; font-weight: 600; border: none;"
        )
        layout.addWidget(title)

        try:
            import pyqtgraph as pg

            pg.setConfigOptions(antialias=True)
            plot = pg.PlotWidget()
            plot.setBackground(_CARD_BG)
            plot.showGrid(x=True, y=True, alpha=0.2)
            plot.setLabel("left", "Mbps")
            plot.setLabel("bottom", "Time", units="s")
            plot.addLegend(offset=(10, 10))
            plot.setMenuEnabled(False)
            plot.setMouseEnabled(x=False, y=True)

            self._download_curve = plot.plot(
                pen=pg.mkPen(_DOWNLOAD_COLOR, width=2), name="Download"
            )
            self._upload_curve = plot.plot(
                pen=pg.mkPen(_UPLOAD_COLOR, width=2), name="Upload"
            )
            self._plot_widget = plot
            layout.addWidget(plot)
        except Exception as exc:  # pyqtgraph missing / backend failure
            logger.warning("PyQtGraph unavailable, graph disabled: %s", exc)
            placeholder = QLabel(
                "Live graph unavailable (PyQtGraph not installed)."
            )
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #8b949e; border: none;")
            layout.addWidget(placeholder)

        return container

    # -- slots (state updates) ------------------------------------------
    @Slot(object)
    def update_bandwidth(self, sample) -> None:
        """
        Update the speed cards and live graph from a bandwidth sample.

        Parameters
        ----------
        sample:
            A :class:`~app.network.monitor.BandwidthSample` (accepted as
            ``object`` so this view does not import the network layer).
        """
        download = float(getattr(sample, "download_mbps", 0.0))
        upload = float(getattr(sample, "upload_mbps", 0.0))

        self._download_card.set_value(download)
        self._upload_card.set_value(upload)

        self._times.append(float(self._sample_index))
        self._download.append(download)
        self._upload.append(upload)
        self._sample_index += 1
        self._redraw_graph()

    @Slot(int, int)
    def update_device_counts(self, connected: int, discovered: int) -> None:
        """Update the connected/discovered device count cards."""
        self._connected_card.set_value(connected)
        self._discovered_card.set_value(discovered)

    def load_history(self, samples: Iterable) -> None:
        """
        Pre-populate the graph from persisted traffic history (e.g. on start).

        Parameters
        ----------
        samples:
            Iterable of objects exposing ``download_mbps`` and
            ``upload_mbps`` attributes, oldest first.
        """
        self._times.clear()
        self._download.clear()
        self._upload.clear()
        self._sample_index = 0
        for sample in samples:
            self._times.append(float(self._sample_index))
            self._download.append(float(getattr(sample, "download_mbps", 0.0)))
            self._upload.append(float(getattr(sample, "upload_mbps", 0.0)))
            self._sample_index += 1
        self._redraw_graph()

    # -- internals -------------------------------------------------------
    def _redraw_graph(self) -> None:
        """Push the current history buffers to the plot curves."""
        if self._download_curve is None or self._upload_curve is None:
            return
        times = list(self._times)
        self._download_curve.setData(times, list(self._download))
        self._upload_curve.setData(times, list(self._upload))