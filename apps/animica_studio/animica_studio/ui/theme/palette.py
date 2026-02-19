from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemePalette:
    mode: str
    bg: str
    surface: str
    elevated: str
    text: str
    muted: str
    border: str
    accent: str
    success: str
    warning: str
    danger: str


def build_palette(mode: str, accent: str) -> ThemePalette:
    if mode == "light":
        return ThemePalette(
            mode="light",
            bg="#f5f7fb",
            surface="#ffffff",
            elevated="#f8fbff",
            text="#18202f",
            muted="#536078",
            border="#d8e0ef",
            accent=accent,
            success="#0f9d58",
            warning="#d27d2d",
            danger="#c23b3b",
        )
    return ThemePalette(
        mode="dark",
        bg="#0f1522",
        surface="#151d2e",
        elevated="#1b2640",
        text="#e7eeff",
        muted="#9daecc",
        border="#2b3653",
        accent=accent,
        success="#3fc17b",
        warning="#f5b967",
        danger="#ff7f7f",
    )
