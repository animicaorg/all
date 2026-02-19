from __future__ import annotations

from animica_studio.ui.theme.palette import ThemePalette


def build_stylesheet(p: ThemePalette) -> str:
    return f"""
* {{
  font-family: "Segoe UI", "SF Pro Text", "Inter", "Ubuntu", sans-serif;
  font-size: 13px;
}}
QMainWindow, QWidget {{ background: {p.bg}; color: {p.text}; }}
QFrame#AppHeader, QFrame#Sidebar {{ background: {p.surface}; border: 1px solid {p.border}; }}
QFrame#Sidebar {{ border-right: 1px solid {p.border}; border-left: none; border-top:none; border-bottom:none; }}
QFrame[card="true"] {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
QLabel[variant="muted"] {{ color: {p.muted}; }}
QLabel[badge="true"] {{
  border-radius: 10px; padding: 2px 8px; background: {p.elevated}; border: 1px solid {p.border};
}}
QPushButton {{ border-radius: 10px; padding: 8px 12px; border: 1px solid {p.border}; background: {p.surface}; color: {p.text}; }}
QPushButton:hover {{ background: {p.elevated}; }}
QPushButton[variant="primary"] {{ background: {p.accent}; color: white; border: none; font-weight: 600; }}
QPushButton[variant="secondary"] {{ background: {p.elevated}; }}
QPushButton[variant="icon"] {{ padding: 6px; min-width: 28px; max-width: 32px; }}
QPushButton[nav="true"] {{ text-align: left; padding: 10px 12px; border:none; }}
QPushButton[nav="true"]:checked {{ background: {p.elevated}; border: 1px solid {p.border}; }}
QLineEdit, QTextEdit, QComboBox {{ background: {p.elevated}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px; }}
QStackedWidget {{ background: transparent; }}
QFrame#InlineError {{ border: 1px solid {p.danger}; background: {p.surface}; border-radius: 10px; }}
QFrame#Toast {{ background: {p.elevated}; border: 1px solid {p.border}; border-radius: 12px; }}
QFrame#Skeleton {{ background: {p.elevated}; border-radius: 8px; border: 1px solid {p.border}; }}
"""
