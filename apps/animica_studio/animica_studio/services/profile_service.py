"""Profile management service for Animica Studio.

This service manages :class:`~animica_studio.models.profile_models.RpcProfile`
instances in memory and persists them to :class:`~animica_studio.storage.config.Config`.

Features
--------
* CRUD operations: add, update, delete, list, get active.
* Profile switching with ``last_used_ts`` tracking.
* Simple observer list for UI subscriptions (no Qt signals inside service).
* Migration / ensure-defaults: creates a default remote profile if none exist.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from animica_studio.models.profile_models import RpcProfile, ProfileType
from animica_studio.storage.config import Config, save_config

log = logging.getLogger(__name__)

# Observer callback type: called with the new active RpcProfile
_ProfileObserver = Callable[["RpcProfile"], None]


class ProfileService:
    """Manage connection profiles backed by :class:`~animica_studio.storage.config.Config`.

    Parameters
    ----------
    config:
        The application configuration object.  Mutations are written back via
        :func:`~animica_studio.storage.config.save_config`.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._observers: list[_ProfileObserver] = []

        # Ensure sane initial state
        self.ensure_defaults()

    # ------------------------------------------------------------------
    # Observer registration
    # ------------------------------------------------------------------

    def subscribe(self, observer: _ProfileObserver) -> None:
        """Register *observer* to be called when the active profile changes."""
        if observer not in self._observers:
            self._observers.append(observer)

    def unsubscribe(self, observer: _ProfileObserver) -> None:
        """Remove a previously registered observer."""
        try:
            self._observers.remove(observer)
        except ValueError:
            pass

    def _notify(self, profile: RpcProfile) -> None:
        for obs in list(self._observers):
            try:
                obs(profile)
            except Exception:  # noqa: BLE001
                log.exception("ProfileService: observer error")

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def list_profiles(self) -> list[RpcProfile]:
        """Return all profiles, ordered by last_used_ts descending."""
        profiles = [RpcProfile.from_dict(d) for d in self._config.rpc_profiles]
        profiles.sort(key=lambda p: p.last_used_ts, reverse=True)
        return profiles

    def get_active(self) -> RpcProfile:
        """Return the active profile, or the first profile if none is explicitly active."""
        profiles = self.list_profiles()
        if not profiles:
            default = RpcProfile.make_default_remote()
            self.add_profile(default)
            self._config.active_profile_id = default.id
            save_config(self._config)
            return default

        active_id = self._config.active_profile_id
        if active_id:
            for p in profiles:
                if p.id == active_id:
                    return p

        # Fall back to most-recently-used
        first = profiles[0]
        self._config.active_profile_id = first.id
        save_config(self._config)
        return first

    def set_active(self, profile_id: str) -> None:
        """Set the active profile by ID and persist.

        Parameters
        ----------
        profile_id:
            The :attr:`~animica_studio.models.profile_models.RpcProfile.id` of the
            profile to activate.

        Raises
        ------
        ValueError
            If no profile with the given ID exists.
        """
        profiles = self.list_profiles()
        target: RpcProfile | None = None
        for p in profiles:
            if p.id == profile_id:
                target = p
                break
        if target is None:
            raise ValueError(f"No profile with id={profile_id!r}")

        target.last_used_ts = time.time()
        self._config.active_profile_id = profile_id
        # Persist updated last_used_ts
        self._save_profiles(profiles, active_id=profile_id)
        log.info("ProfileService: active profile set to %r (%s)", target.name, profile_id)
        self._notify(target)

    def add_profile(self, profile: RpcProfile) -> None:
        """Add a new profile and persist.

        If a profile with the same ID already exists it is replaced.
        """
        profiles = self.list_profiles()
        # Replace if ID exists
        for i, p in enumerate(profiles):
            if p.id == profile.id:
                profiles[i] = profile
                self._save_profiles(profiles)
                return
        profiles.append(profile)
        self._save_profiles(profiles)
        log.info("ProfileService: added profile %r (%s)", profile.name, profile.id)

    def update_profile(self, profile: RpcProfile) -> None:
        """Update an existing profile and persist.

        Raises
        ------
        ValueError
            If no profile with the given ID exists.
        """
        profiles = self.list_profiles()
        for i, p in enumerate(profiles):
            if p.id == profile.id:
                profiles[i] = profile
                self._save_profiles(profiles)
                # If updating active profile, notify observers
                if profile.id == self._config.active_profile_id:
                    self._notify(profile)
                return
        raise ValueError(f"Cannot update: no profile with id={profile.id!r}")

    def delete_profile(self, profile_id: str) -> None:
        """Delete a profile by ID.

        Raises
        ------
        ValueError
            If the profile is the only one remaining, or does not exist.
        """
        profiles = self.list_profiles()
        if len(profiles) <= 1:
            raise ValueError("Cannot delete the last remaining profile")
        remaining = [p for p in profiles if p.id != profile_id]
        if len(remaining) == len(profiles):
            raise ValueError(f"No profile with id={profile_id!r}")

        # Adjust active if deleted
        if self._config.active_profile_id == profile_id:
            self._config.active_profile_id = remaining[0].id
            log.info("ProfileService: active profile switched to %r after deletion", remaining[0].id)

        self._save_profiles(remaining)
        log.info("ProfileService: deleted profile %r", profile_id)

    # ------------------------------------------------------------------
    # Migration / ensure defaults
    # ------------------------------------------------------------------

    def ensure_defaults(self) -> None:
        """Migrate legacy config data and ensure at least one profile exists."""
        if not self._config.rpc_profiles:
            # Create a sensible default profile
            default = RpcProfile.make_default_remote()
            self._config.rpc_profiles = [default.to_dict()]
            self._config.active_profile_id = default.id
            save_config(self._config)
            log.info("ProfileService: created default remote profile")

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _save_profiles(
        self, profiles: list[RpcProfile], active_id: str | None = None
    ) -> None:
        self._config.rpc_profiles = [p.to_dict() for p in profiles]
        if active_id is not None:
            self._config.active_profile_id = active_id
        save_config(self._config)

    # ------------------------------------------------------------------
    # Public state accessors
    # ------------------------------------------------------------------

    def get_active_profile_id(self) -> str | None:
        """Return the active profile ID from config."""
        return self._config.active_profile_id

    def mark_first_run_complete(self) -> None:
        """Mark the first-run wizard as completed and persist."""
        self._config.first_run_completed = True
        save_config(self._config)
