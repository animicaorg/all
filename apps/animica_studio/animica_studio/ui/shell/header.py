from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton

from animica_studio.ui.shell.icon_provider import IconProvider


class HeaderBar(QFrame):
    open_settings = Signal()
    open_palette = Signal()
    open_profiles = Signal()

    def __init__(self, icons: IconProvider) -> None:
        super().__init__()
        self.setObjectName("AppHeader")
        self.setFixedHeight(56)
        self._logo = QLabel()
        self._logo.setPixmap(icons.logo_pixmap())
        self._title = QLabel("Animica Studio")
        self._rpc = QLabel("RPC: —")
        self._rpc.setProperty("variant", "muted")
        self._chain = QLabel("Chain: —")
        self._chain.setProperty("variant", "muted")
        self._dot = QLabel("●")
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(180)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.addWidget(self._logo)
        lay.addWidget(self._title)
        lay.addSpacing(8)
        lay.addWidget(self._profile_combo)
        lay.addWidget(self._rpc)
        lay.addWidget(self._chain)
        lay.addWidget(self._dot)
        lay.addStretch()
        palette_btn = QPushButton("⌘K")
        palette_btn.setProperty("variant", "icon")
        palette_btn.clicked.connect(self.open_palette.emit)
        lay.addWidget(palette_btn)
        profiles = QPushButton("Profiles")
        profiles.clicked.connect(self.open_profiles.emit)
        lay.addWidget(profiles)
        settings = QPushButton("⚙")
        settings.setProperty("variant", "icon")
        settings.clicked.connect(self.open_settings.emit)
        lay.addWidget(settings)

    def profile_combo(self) -> QComboBox:
        return self._profile_combo

    def set_connection(self, ok: bool) -> None:
        self._dot.setStyleSheet(f"color: {'#3fc17b' if ok else '#ff7f7f'};")
        if ok:
            anim = QPropertyAnimation(self._logo, b"geometry", self)
            g = self._logo.geometry()
            anim.setDuration(600)
            anim.setKeyValueAt(0, g)
            anim.setKeyValueAt(0.5, QRect(g.x(), g.y() - 1, g.width(), g.height()))
            anim.setKeyValueAt(1, g)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def set_meta(self, rpc: str, chain: str) -> None:
        self._rpc.setText(f"RPC: {rpc}")
        self._chain.setText(f"Chain: {chain}")
