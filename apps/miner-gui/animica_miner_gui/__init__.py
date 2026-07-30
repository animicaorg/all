"""Animica GUI Miner - Production-quality Qt desktop miner application."""

__version__ = "9.0.8"

# Version of the unified `animica` package this GUI is built to bundle. The
# build scripts assert the installed package matches before freezing, so a
# shipped binary always reports the node/miner code it actually carries.
BUNDLED_ANIMICA_VERSION = "9.0.8"


def bundled_animica_version() -> str:
    """Return the `animica` version actually importable in this build.

    Falls back to :data:`BUNDLED_ANIMICA_VERSION` when the package metadata is
    unavailable (e.g. a source checkout without an install).
    """
    try:
        from importlib.metadata import version

        return version("animica")
    except Exception:
        return BUNDLED_ANIMICA_VERSION
