"""Animica Studio — desktop application for the Animica blockchain."""

try:
    from animica_studio._version import __version__  # type: ignore[assignment]
except Exception:  # noqa: BLE001
    __version__ = "0.1.0"

__app_name__ = "Animica Studio"
__org_name__ = "Animica"
