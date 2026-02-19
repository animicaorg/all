from __future__ import annotations

import math

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QRadialGradient
from PySide6.QtWidgets import QWidget


class HeroVisual(QWidget):
    def __init__(self, mode: str = "balanced", reduced_motion: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._t = 0.0
        self._mode = mode
        self._reduced_motion = reduced_motion
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.setMinimumHeight(180)

    def set_effect_mode(self, mode: str, reduced_motion: bool) -> None:
        self._mode = mode
        self._reduced_motion = reduced_motion
        self.update()

    @staticmethod
    def has_3d_support() -> bool:
        try:
            from PySide6 import QtOpenGLWidgets  # noqa: F401
            return True
        except Exception:
            return False

    def _tick(self) -> None:
        if self._mode == "off" or self._reduced_motion:
            return
        self._t += 0.04
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#10192f"))
        w = self.width()
        h = self.height()
        for i in range(3):
            phase = self._t * (0.7 + i * 0.15)
            x = (w * (0.2 + i * 0.28)) + math.sin(phase) * 18
            y = (h * 0.5) + math.cos(phase * 1.4) * (8 + i * 4)
            r = 44 - i * 8
            grad = QRadialGradient(x, y, r)
            grad.setColorAt(0, QColor(91, 140, 255, 110 - i * 24))
            grad.setColorAt(1, QColor(16, 25, 47, 10))
            p.setBrush(grad)
            p.setPen(QColor(91, 140, 255, 80))
            p.drawEllipse(int(x - r), int(y - r), int(r * 2), int(r * 2))
        p.setPen(QColor("#d8e4ff"))
        p.drawText(24, 30, "Animica Network")
        p.end()
