from __future__ import annotations

from animica_studio.ui.theme.palette import ThemePalette


def build_stylesheet(p: ThemePalette) -> str:
    """Build the global QSS stylesheet from the active palette."""
    return f"""
/* ── Global reset ────────────────────────────────────────────────── */
* {{
  font-family: "Segoe UI", "SF Pro Text", "Inter", "Helvetica Neue",
               "Ubuntu", "Noto Sans", sans-serif;
  font-size: 13px;
  outline: none;
}}

/* ── Base ────────────────────────────────────────────────────────── */
QMainWindow,
QWidget {{
  background: {p.bg};
  color: {p.text};
}}

/* ── App chrome ──────────────────────────────────────────────────── */
QFrame#AppHeader {{
  background: {p.surface};
  border-bottom: 1px solid {p.border};
}}
QFrame#Sidebar {{
  background: {p.surface};
  border-right: 1px solid {p.border};
}}

/* ── Cards ───────────────────────────────────────────────────────── */
QFrame[card="true"] {{
  background: {p.surface};
  border: 1px solid {p.border};
  border-radius: 14px;
}}

/* ── Typography helpers ──────────────────────────────────────────── */
QLabel[variant="muted"] {{
  color: {p.muted};
}}
QLabel[badge="true"] {{
  border-radius: 10px;
  padding: 2px 9px;
  background: {p.elevated};
  border: 1px solid {p.border};
  font-size: 11px;
  font-weight: 600;
}}
QLabel[navSection="true"] {{
  color: {p.muted};
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.7px;
  padding: 10px 8px 4px 8px;
}}

/* ── Buttons ─────────────────────────────────────────────────────── */
QPushButton {{
  border-radius: 10px;
  padding: 7px 14px;
  border: 1px solid {p.border};
  background: {p.surface};
  color: {p.text};
  font-weight: 500;
  min-height: 20px;
}}
QPushButton:hover {{
  background: {p.elevated};
  border-color: {p.accent};
}}
QPushButton:pressed {{
  background: {p.bg};
}}
QPushButton:disabled {{
  color: {p.muted};
  border-color: {p.border};
  background: {p.surface};
}}
QPushButton[variant="primary"] {{
  background: {p.accent};
  color: #ffffff;
  border: none;
  font-weight: 700;
}}
QPushButton[variant="primary"]:hover {{
  background: {_lighten(p.accent, 18)};
}}
QPushButton[variant="primary"]:pressed {{
  background: {_darken(p.accent, 18)};
}}
QPushButton[variant="secondary"] {{
  background: {p.elevated};
  border-color: {p.border};
}}
QPushButton[variant="icon"] {{
  padding: 4px 6px;
  min-width: 28px;
  max-width: 36px;
  border-radius: 8px;
  font-size: 14px;
  border-color: transparent;
  background: transparent;
}}
QPushButton[variant="icon"]:hover {{
  background: {p.elevated};
  border-color: {p.border};
}}

/* ── Nav buttons (sidebar) ────────────────────────────────────────── */
QPushButton[nav="true"] {{
  text-align: left;
  padding: 9px 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  font-weight: 500;
}}
QPushButton[nav="true"]:hover {{
  background: {p.elevated};
}}
QPushButton[nav="true"]:checked {{
  background: {p.elevated};
  border: 1px solid {p.border};
  color: {p.accent};
  font-weight: 600;
}}

/* ── Inputs ──────────────────────────────────────────────────────── */
QLineEdit,
QTextEdit,
QPlainTextEdit {{
  background: {p.elevated};
  border: 1px solid {p.border};
  border-radius: 9px;
  padding: 6px 10px;
  color: {p.text};
  selection-background-color: {p.accent};
}}
QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus {{
  border-color: {p.accent};
}}
QComboBox {{
  background: {p.elevated};
  border: 1px solid {p.border};
  border-radius: 9px;
  padding: 5px 10px;
  color: {p.text};
}}
QComboBox::drop-down {{
  border: none;
  padding-right: 8px;
}}
QComboBox QAbstractItemView {{
  background: {p.surface};
  border: 1px solid {p.border};
  selection-background-color: {p.elevated};
  selection-color: {p.text};
}}
QSpinBox {{
  background: {p.elevated};
  border: 1px solid {p.border};
  border-radius: 9px;
  padding: 5px 8px;
  color: {p.text};
}}

/* ── Group boxes ─────────────────────────────────────────────────── */
QGroupBox {{
  font-weight: 600;
  border: 1px solid {p.border};
  border-radius: 12px;
  margin-top: 8px;
  padding-top: 6px;
}}
QGroupBox::title {{
  subcontrol-origin: margin;
  subcontrol-position: top left;
  padding: 0 8px;
  left: 12px;
  color: {p.muted};
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
}}

/* ── Scrollbars ──────────────────────────────────────────────────── */
QScrollBar:vertical {{
  background: {p.bg};
  width: 8px;
  border-radius: 4px;
}}
QScrollBar::handle:vertical {{
  background: {p.border};
  border-radius: 4px;
  min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
  background: {p.muted};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
  height: 0;
}}
QScrollBar:horizontal {{
  background: {p.bg};
  height: 8px;
  border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
  background: {p.border};
  border-radius: 4px;
  min-width: 24px;
}}

/* ── Tables ──────────────────────────────────────────────────────── */
QTableWidget {{
  background: {p.surface};
  gridline-color: {p.border};
  border: 1px solid {p.border};
  border-radius: 10px;
}}
QTableWidget::item:selected {{
  background: {p.elevated};
  color: {p.text};
}}
QHeaderView::section {{
  background: {p.bg};
  color: {p.muted};
  padding: 5px 8px;
  border: none;
  border-bottom: 1px solid {p.border};
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}

/* ── List widgets ────────────────────────────────────────────────── */
QListWidget {{
  background: {p.surface};
  border: 1px solid {p.border};
  border-radius: 10px;
}}
QListWidget::item:selected {{
  background: {p.elevated};
  color: {p.text};
  border-radius: 6px;
}}

/* ── Tabs ────────────────────────────────────────────────────────── */
QTabWidget::pane {{
  border: 1px solid {p.border};
  border-radius: 10px;
  background: {p.surface};
}}
QTabBar::tab {{
  background: transparent;
  color: {p.muted};
  padding: 6px 16px;
  border-bottom: 2px solid transparent;
  font-weight: 500;
}}
QTabBar::tab:selected {{
  color: {p.text};
  border-bottom: 2px solid {p.accent};
  font-weight: 600;
}}
QTabBar::tab:hover {{
  color: {p.text};
}}

/* ── Stacked widget ──────────────────────────────────────────────── */
QStackedWidget {{
  background: transparent;
}}

/* ── Error / notification frames ─────────────────────────────────── */
QFrame#InlineError {{
  border: 1px solid {p.danger};
  background: {p.surface};
  border-radius: 10px;
  padding: 2px;
}}
QFrame#Toast {{
  background: {p.elevated};
  border: 1px solid {p.border};
  border-radius: 14px;
}}

/* ── Skeleton loader ─────────────────────────────────────────────── */
QFrame#Skeleton {{
  background: {p.elevated};
  border-radius: 8px;
  border: 1px solid {p.border};
}}

/* ── Dialogs ─────────────────────────────────────────────────────── */
QDialog {{
  background: {p.surface};
}}
QDialogButtonBox QPushButton {{
  min-width: 80px;
}}
QMessageBox {{
  background: {p.surface};
}}
"""


# ---------------------------------------------------------------------------
# Tiny helpers to produce lighter / darker hex colours
# ---------------------------------------------------------------------------

def _clamp(v: int) -> int:
    return max(0, min(255, v))


def _lighten(hex_color: str, amount: int) -> str:
    r, g, b = _parse_hex(hex_color)
    return f"#{_clamp(r + amount):02x}{_clamp(g + amount):02x}{_clamp(b + amount):02x}"


def _darken(hex_color: str, amount: int) -> str:
    return _lighten(hex_color, -amount)


def _parse_hex(hex_color: str) -> tuple[int, int, int]:
    c = hex_color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
