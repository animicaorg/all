from __future__ import annotations

import math
import random

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPaintEvent, QRadialGradient
from PySide6.QtWidgets import QWidget

_PARTICLE_COUNT = 28


class HeroVisual(QWidget):
    """Lightweight 2D hero widget with parallax orbs, particles, and gradient.

    Works on any platform without GPU. Falls back to a static gradient when
    ``mode == "off"`` or ``reduced_motion`` is ``True``.
    """

    def __init__(self, mode: str = "balanced", reduced_motion: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = 0.0
        self._mode = mode
        self._reduced_motion = reduced_motion
        self._particles: list[dict] = []
        self._init_particles()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps — lightweight
        self.setMinimumHeight(180)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_particles(self) -> None:
        rng = random.Random(42)
        self._particles = [
            {
                "x": rng.random(),
                "y": rng.random(),
                "vx": (rng.random() - 0.5) * 0.003,
                "vy": (rng.random() - 0.5) * 0.002,
                "r": rng.randint(1, 3),
                "alpha": rng.randint(60, 160),
            }
            for _ in range(_PARTICLE_COUNT)
        ]

    def _tick(self) -> None:
        if self._mode == "off" or self._reduced_motion:
            return
        self._t += 0.04
        if self._mode in ("balanced", "high"):
            for p in self._particles:
                p["x"] = (p["x"] + p["vx"]) % 1.0
                p["y"] = (p["y"] + p["vy"]) % 1.0
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        # Background gradient
        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0, QColor("#0a1120"))
        bg.setColorAt(1, QColor("#111a2e"))
        painter.fillRect(self.rect(), bg)

        if self._mode == "off":
            # Static title only
            painter.setPen(QColor("#9daecc"))
            painter.drawText(18, 26, "Animica Network")
            painter.end()
            return

        # Parallax orbs (3 layers)
        orb_specs = [
            (0.18, 0.50, 64, QColor(91, 140, 255, 90)),
            (0.50, 0.50, 52, QColor(139, 92, 246, 70)),
            (0.82, 0.50, 44, QColor(20, 184, 166, 60)),
        ]
        for idx, (nx, ny, base_r, color) in enumerate(orb_specs):
            phase = self._t * (0.6 + idx * 0.18)
            cx = w * nx + math.sin(phase) * (12 + idx * 5)
            cy = h * ny + math.cos(phase * 1.3) * (6 + idx * 4)
            r = base_r + math.sin(phase * 0.7) * 6
            grad = QRadialGradient(cx, cy, r)
            grad.setColorAt(0, color)
            grad.setColorAt(0.6, QColor(color.red(), color.green(), color.blue(), 30))
            grad.setColorAt(1, QColor(0, 0, 0, 0))
            painter.setBrush(grad)
            painter.setPen(QColor(0, 0, 0, 0))
            painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # Network lines (high mode only)
        if self._mode == "high":
            line_color = QColor(91, 140, 255, 35)
            painter.setPen(line_color)
            pts = [
                (int(w * 0.18 + math.sin(self._t * 0.6) * 12), int(h * 0.5)),
                (int(w * 0.50 + math.sin(self._t * 0.78) * 8), int(h * 0.5)),
                (int(w * 0.82 + math.sin(self._t * 0.96) * 14), int(h * 0.5)),
            ]
            for i in range(len(pts) - 1):
                painter.drawLine(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])

        # Particles (balanced + high)
        if self._mode in ("balanced", "high"):
            for p in self._particles:
                px, py = int(p["x"] * w), int(p["y"] * h)
                r = p["r"]
                alpha = int(p["alpha"] * (0.5 + 0.5 * math.sin(self._t + p["x"] * 6)))
                c = QColor(160, 190, 255, alpha)
                painter.setBrush(c)
                painter.setPen(QColor(0, 0, 0, 0))
                painter.drawEllipse(px - r, py - r, r * 2, r * 2)

        # Title text
        painter.setPen(QColor("#d8e4ff"))
        font = painter.font()
        font.setPointSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(18, 28, "Animica Network")

        # Subtle status text
        painter.setPen(QColor("#5b8cff"))
        small_font = painter.font()
        small_font.setPointSize(9)
        small_font.setWeight(QFont.Weight.Normal)
        painter.setFont(small_font)
        painter.drawText(18, 46, "● Live")

        painter.end()
