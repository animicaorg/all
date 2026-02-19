from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon, QPainter, QPixmap


class IconProvider:
    def __init__(self) -> None:
        self._icon_dir = Path(__file__).resolve().parents[1] / "resources" / "icons"

    def icon(self, name: str, _tint: str = "") -> QIcon:
        path = self._icon_dir / f"{name}.svg"
        if not path.exists():
            return QIcon()
        return QIcon(str(path))

    def logo_pixmap(self, size: int = 18) -> QPixmap:
        pix = QPixmap(size, size)
        pix.fill("transparent")
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen("#5b8cff")
        p.drawEllipse(2, 2, size - 4, size - 4)
        p.drawText(pix.rect(), 0x84, "A")
        p.end()
        return pix
