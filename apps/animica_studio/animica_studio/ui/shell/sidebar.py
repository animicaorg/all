from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout


class Sidebar(QFrame):
    navigate = Signal(int)
    toggled = Signal(bool)  # True = expanded

    _EXPANDED_W = 220
    _COLLAPSED_W = 60

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self._expanded = True
        self._buttons: list[QPushButton] = []
        self._full_labels: list[str] = []
        self._anim: QPropertyAnimation | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Toggle button row at top
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(8, 8, 8, 4)
        self._toggle_btn = QPushButton("◀")
        self._toggle_btn.setProperty("variant", "icon")
        self._toggle_btn.setFixedSize(32, 28)
        self._toggle_btn.clicked.connect(self.toggle)
        toggle_row.addStretch()
        toggle_row.addWidget(self._toggle_btn)
        outer.addLayout(toggle_row)

        # Nav buttons
        nav_frame = QFrame()
        lay = QVBoxLayout(nav_frame)
        lay.setContentsMargins(8, 4, 8, 10)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout = lay
        outer.addWidget(nav_frame, 1)

        self.setFixedWidth(self._EXPANDED_W)

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
        target = self._EXPANDED_W if self._expanded else self._COLLAPSED_W
        self._toggle_btn.setText("◀" if self._expanded else "▶")
        for i, b in enumerate(self._buttons):
            if self._expanded:
                b.setText(self._full_labels[i])
            else:
                # Keep only the icon character (before the first space)
                b.setText(self._full_labels[i].split("  ")[0])
        if animate:
            if self._anim is not None:
                self._anim.stop()
            self._anim = QPropertyAnimation(self, b"minimumWidth", self)
            self._anim.setDuration(200)
            self._anim.setStartValue(self.width())
            self._anim.setEndValue(target)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.finished.connect(lambda: self.setFixedWidth(target))
            self._anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        else:
            self.setFixedWidth(target)
        self.toggled.emit(self._expanded)
