from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, Property, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", "true")
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(14, 14, 14, 14)


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        l = QVBoxLayout(self)
        t = QLabel(title)
        t.setStyleSheet("font-size:16px;font-weight:600;")
        l.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setProperty("variant", "muted")
            l.addWidget(s)


class Badge(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setProperty("badge", "true")


class ThemedButton(QPushButton):
    def __init__(self, text: str, variant: str = "secondary") -> None:
        super().__init__(text)
        self.setProperty("variant", variant)


class InlineError(QFrame):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__()
        self.setObjectName("InlineError")
        lay = QHBoxLayout(self)
        lay.addWidget(QLabel(f"⚠ {message}"))
        copy_btn = QPushButton("Copy details")
        copy_btn.clicked.connect(lambda: self.window().clipboard().setText(details or message))
        lay.addWidget(copy_btn)
        lay.addStretch()


class Toast(QFrame):
    def __init__(self, parent: QWidget, text: str) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self._offset = QPoint(0, 12)
        l = QHBoxLayout(self)
        l.addWidget(QLabel(text))
        self.adjustSize()
        self.hide()

    def show_toast(self, timeout_ms: int = 2500, animate: bool = True) -> None:
        self.show()
        if animate:
            a = QPropertyAnimation(self, b"pos", self)
            a.setStartValue(self.pos() + self._offset)
            a.setEndValue(self.pos())
            a.setDuration(180)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        QTimer.singleShot(timeout_ms, self.hide)


class SkeletonLoader(QFrame):
    def __init__(self, width: int = 240, height: int = 16) -> None:
        super().__init__()
        self.setObjectName("Skeleton")
        self.setFixedSize(width, height)
