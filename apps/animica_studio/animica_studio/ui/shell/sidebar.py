from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import QFrame, QPushButton, QVBoxLayout


class Sidebar(QFrame):
    navigate = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self._expanded = True
        self._buttons: list[QPushButton] = []
        self._full_labels: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout = lay
        self.setFixedWidth(220)

    def add_item(self, label: str, icon: str, index: int) -> None:
        btn = QPushButton(f"{icon}  {label}")
        btn.setCheckable(True)
        btn.setProperty("nav", "true")
        btn.clicked.connect(lambda _c=False, i=index: self.navigate.emit(i))
        self._layout.addWidget(btn)
        self._buttons.append(btn)
        self._full_labels.append(f"{icon}  {label}")

    def set_active(self, index: int) -> None:
        for i, b in enumerate(self._buttons):
            b.setChecked(i == index)

    def toggle(self, animate: bool = True) -> None:
        self._expanded = not self._expanded
        target = 220 if self._expanded else 68
        for i, b in enumerate(self._buttons):
            b.setText(self._full_labels[i] if self._expanded else self._full_labels[i].split("  ")[0])
        if animate:
            anim = QPropertyAnimation(self, b"minimumWidth", self)
            anim.setDuration(200)
            anim.setStartValue(self.width())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self.setFixedWidth(target)
