"""Reusable metric card widget.
​
A compact, dark-themed card that displays a single labelled metric -- a title
caption above a large value, with an optional unit suffix. Extracted from the
Dashboard so any page (Dashboard, History, future Alerts, etc.) can reuse the
same styling and behaviour.
​
Example
-------
    card = MetricCard("Download", unit="Mbps", accent="#4ea1ff")
    card.set_value(12.34)     # -> "12.34 Mbps"
    card.set_value(7)         # -> "7"
    card.reset()              # -> "--"
​
The widget is intentionally a pure view: it holds no state beyond what is
displayed and performs no I/O.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# Dark-theme palette (kept local so the widget is self-contained).
_CARD_BG = "#1e1f26"
_CARD_BORDER = "#2c2e38"
_PLACEHOLDER = "--"


class MetricCard(QFrame):
    """
    A compact card showing a single labelled metric.

    Parameters
    ----------
    title:
        Caption shown above the value (e.g. ``"Download"``). Rendered in
        uppercase.
    unit:
        Optional unit suffix rendered after the value (e.g. ``"Mbps"``).
    accent:
        Hex colour used for the value text.
    parent:
        Optional parent widget.
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

        self._value_label = QLabel(_PLACEHOLDER)
        self._value_label.setStyleSheet(
            f"color: {accent}; font-size: 30px; font-weight: 700; border: none;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)
        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)
        layout.addStretch(1)

    # -- public API ------------------------------------------------------
    def set_value(self, value: str | float | int) -> None:
        """Update the displayed value, appending the unit if configured."""
        if isinstance(value, float):
            text = f"{value:.2f}"
        else:
            text = str(value)
        if self._unit:
            text = f"{text} {self._unit}"
        self._value_label.setText(text)

    def set_title(self, title: str) -> None:
        """Update the card's title caption."""
        self._title_label.setText(title.upper())

    def set_accent(self, accent: str) -> None:
        """Change the colour of the value text."""
        self._value_label.setStyleSheet(
            f"color: {accent}; font-size: 30px; font-weight: 700; border: none;"
        )

    def reset(self) -> None:
        """Clear the value back to the placeholder."""
        self._value_label.setText(_PLACEHOLDER)