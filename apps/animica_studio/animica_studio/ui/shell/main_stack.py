from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget


class AnimatedStack(QStackedWidget):
    def setCurrentIndexAnimated(self, index: int, reduced_motion: bool = False) -> None:
        if reduced_motion:
            self.setCurrentIndex(index)
            return
        old = self.currentWidget()
        self.setCurrentIndex(index)
        new = self.currentWidget()
        if old:
            eff = QGraphicsOpacityEffect(old)
            old.setGraphicsEffect(eff)
            f = QPropertyAnimation(eff, b"opacity", self)
            f.setDuration(140)
            f.setStartValue(1.0)
            f.setEndValue(0.2)
            f.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        if new:
            eff2 = QGraphicsOpacityEffect(new)
            new.setGraphicsEffect(eff2)
            fi = QPropertyAnimation(eff2, b"opacity", self)
            fi.setDuration(180)
            fi.setStartValue(0.0)
            fi.setEndValue(1.0)
            fi.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            p = QPropertyAnimation(new, b"pos", self)
            p.setDuration(180)
            p.setStartValue(new.pos() + QPoint(14, 0))
            p.setEndValue(new.pos())
            p.setEasingCurve(QEasingCurve.Type.OutCubic)
            p.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
