from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, Property, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", "true")
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(14, 14, 14, 14)


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size:18px;font-weight:700;letter-spacing:-0.3px;")
        lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setProperty("variant", "muted")
            lay.addWidget(s)


class Badge(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setProperty("badge", "true")


class ThemedButton(QPushButton):
    """Button with an explicit design-system variant (primary / secondary / icon)."""

    def __init__(self, text: str, variant: str = "secondary") -> None:
        super().__init__(text)
        self.setProperty("variant", variant)


# Convenience aliases that make intent explicit in page code.
class PrimaryButton(QPushButton):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setProperty("variant", "primary")


class SecondaryButton(QPushButton):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setProperty("variant", "secondary")


class IconButton(QPushButton):
    def __init__(self, icon_text: str, tooltip: str = "") -> None:
        super().__init__(icon_text)
        self.setProperty("variant", "icon")
        if tooltip:
            self.setToolTip(tooltip)


class InlineError(QFrame):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__()
        self.setObjectName("InlineError")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        icon = QLabel("⚠")
        icon.setStyleSheet("font-size:14px;")
        lay.addWidget(icon)
        msg_label = QLabel(str(message))
        msg_label.setWordWrap(True)
        lay.addWidget(msg_label, 1)
        _details = str(details) if details else str(message)
        copy_btn = SecondaryButton("Copy")
        copy_btn.setFixedWidth(54)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(_details))
        lay.addWidget(copy_btn)

    def update_message(self, message: str, details: str = "") -> None:
        """Replace the displayed error text in-place."""
        labels = self.findChildren(QLabel)
        for lbl in labels:
            if lbl.text() not in ("⚠",):
                lbl.setText(str(message))
                break


class EmptyState(QWidget):
    """Friendly empty-state widget: icon + title + subtitle."""

    def __init__(self, icon: str, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 32, 24, 32)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size:40px;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(icon_lbl)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size:15px;font-weight:600;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title_lbl)
        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setProperty("variant", "muted")
            sub_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            sub_lbl.setWordWrap(True)
            lay.addWidget(sub_lbl)


class Toast(QFrame):
    def __init__(self, parent: QWidget, text: str) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self._offset = QPoint(0, 12)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        self._label = QLabel(str(text))
        lay.addWidget(self._label)
        self.adjustSize()
        self.hide()

    def show_toast(self, timeout_ms: int = 2500, animate: bool = True) -> None:
        self.show()
        self.raise_()
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
