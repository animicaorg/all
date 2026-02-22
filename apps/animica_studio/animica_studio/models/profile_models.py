"""Data models for Animica Studio connection profiles."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from animica_studio.util.paths import default_chain_data_dir


class ProfileType(str, Enum):
    """Connection profile type."""

    REMOTE_RPC = "remote_rpc"
    LOCAL_NODE = "local_node"


_VALID_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


def validate_rpc_url(url: str) -> str:
    """Validate that *url* is an HTTP/HTTPS URL.

    Raises
    ------
    ValueError
        If the URL does not start with http:// or https://.
    """
    stripped = url.strip()
    if not _VALID_HTTP_RE.match(stripped):
        raise ValueError(f"RPC URL must start with http:// or https://, got: {stripped!r}")
    return stripped


def validate_explorer_base_url(url: str | None) -> str:
    """Validate and normalize an optional explorer base URL.

    Returns an empty string when unset; otherwise returns an HTTP(S) URL with
    trailing slashes removed.
    """
    stripped = str(url or "").strip()
    if not stripped:
        return ""
    if not _VALID_HTTP_RE.match(stripped):
        raise ValueError(f"Explorer URL must start with http:// or https://, got: {stripped!r}")
    return stripped.rstrip("/")


def sanitize_name(name: str | None, fallback: str = "Unnamed") -> str:
    """Trim *name* and return *fallback* if the result is empty."""
    if not name:
        return fallback
    stripped = name.strip()
    return stripped if stripped else fallback


@dataclass
class RpcProfile:
    """A named connection profile for Animica Studio.

    Attributes
    ----------
    id:
        Unique identifier (UUID4).
    name:
        Human-friendly profile name.
    type:
        :class:`ProfileType` – ``REMOTE_RPC`` or ``LOCAL_NODE``.
    rpc_url:
        Full HTTP/HTTPS URL of the RPC endpoint.
    chain_id_expected:
        Expected chain ID; validated against the node on connect.
    node_start_cmd:
        Command to start a local node (``LOCAL_NODE`` only).
    node_datadir:
        Filesystem path to the local node data directory (``LOCAL_NODE`` only).
    node_rpc_url:
        Local node's RPC URL, if different from ``rpc_url`` (``LOCAL_NODE`` only).
    created_ts:
        Unix timestamp when the profile was created.
    last_used_ts:
        Unix timestamp when the profile was last activated.
    notes:
        Optional free-text notes.
    """

    id: str
    name: str
    type: ProfileType
    rpc_url: str
    chain_id_expected: int
    node_start_cmd: list[str] | None = None
    node_datadir: str | None = None
    node_datadir_custom: bool = False
    node_rpc_url: str | None = None
    explorer_base_url: str = ""
    created_ts: float = field(default_factory=time.time)
    last_used_ts: float = field(default_factory=time.time)
    notes: str | None = None

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def is_local(self) -> bool:
        """Return ``True`` if this is a local-node profile."""
        return self.type == ProfileType.LOCAL_NODE

    def is_remote(self) -> bool:
        """Return ``True`` if this is a remote RPC profile."""
        return self.type == ProfileType.REMOTE_RPC

    def effective_rpc_url(self) -> str:
        """Return the effective RPC URL (node_rpc_url if set and local, else rpc_url)."""
        if self.is_local() and self.node_rpc_url:
            return self.node_rpc_url
        return self.rpc_url

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the profile to a plain dict suitable for JSON storage."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "rpc_url": self.rpc_url,
            "chain_id_expected": self.chain_id_expected,
            "node_start_cmd": self.node_start_cmd,
            "node_datadir": self.node_datadir,
            "node_datadir_custom": self.node_datadir_custom,
            "node_rpc_url": self.node_rpc_url,
            "explorer_base_url": self.explorer_base_url,
            "created_ts": self.created_ts,
            "last_used_ts": self.last_used_ts,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RpcProfile":
        """Deserialise a profile from a plain dict, healing missing/invalid fields.

        Parameters
        ----------
        d:
            Raw dict (e.g. from JSON storage).

        Returns
        -------
        RpcProfile
            A fully-initialised profile; missing fields receive safe defaults.
        """
        profile_id = str(d.get("id") or uuid.uuid4())

        raw_type = d.get("type", ProfileType.REMOTE_RPC.value)
        try:
            ptype = ProfileType(raw_type)
        except ValueError:
            ptype = ProfileType.REMOTE_RPC

        raw_url = str(d.get("rpc_url") or "https://mainnet.animica.org/rpc")
        try:
            rpc_url = validate_rpc_url(raw_url)
        except ValueError:
            rpc_url = "https://mainnet.animica.org/rpc"

        chain_id_raw = d.get("chain_id_expected", 1)
        try:
            chain_id = int(chain_id_raw)
        except (TypeError, ValueError):
            chain_id = 1

        raw_cmd = d.get("node_start_cmd")
        node_start_cmd: list[str] | None = None
        if isinstance(raw_cmd, list):
            node_start_cmd = [str(s) for s in raw_cmd]

        node_datadir = str(d["node_datadir"]) if d.get("node_datadir") else None
        node_datadir_custom = bool(d.get("node_datadir_custom", bool(node_datadir)))
        if not node_datadir_custom:
            node_datadir = str(default_chain_data_dir(chain_id))
        node_rpc_url_raw = d.get("node_rpc_url")
        node_rpc_url: str | None = None
        if node_rpc_url_raw:
            try:
                node_rpc_url = validate_rpc_url(str(node_rpc_url_raw))
            except ValueError:
                node_rpc_url = None

        explorer_raw = d.get("explorer_base_url", "")
        try:
            explorer_base_url = validate_explorer_base_url(str(explorer_raw))
        except ValueError:
            explorer_base_url = ""

        now = time.time()
        created_ts_raw = d.get("created_ts", now)
        try:
            created_ts = float(created_ts_raw)
        except (TypeError, ValueError):
            created_ts = now

        last_used_ts_raw = d.get("last_used_ts", now)
        try:
            last_used_ts = float(last_used_ts_raw)
        except (TypeError, ValueError):
            last_used_ts = now

        name = sanitize_name(d.get("name"), fallback="Unnamed Profile")
        notes_raw = d.get("notes")
        notes = str(notes_raw) if notes_raw else None

        return cls(
            id=profile_id,
            name=name,
            type=ptype,
            rpc_url=rpc_url,
            chain_id_expected=chain_id,
            node_start_cmd=node_start_cmd,
            node_datadir=node_datadir,
            node_rpc_url=node_rpc_url,
            explorer_base_url=explorer_base_url,
            node_datadir_custom=node_datadir_custom,
            created_ts=created_ts,
            last_used_ts=last_used_ts,
            notes=notes,
        )

    @classmethod
    def make_default_remote(cls, name: str = "Mainnet Remote") -> "RpcProfile":
        """Create a default remote RPC profile."""
        return cls(
            id=str(uuid.uuid4()),
            name=name,
            type=ProfileType.REMOTE_RPC,
            rpc_url="https://mainnet.animica.org/rpc",
            chain_id_expected=1,
        )

    @classmethod
    def make_default_local(
        cls, datadir: str | None = None, chain_id: int = 1, name: str = "Local Node"
    ) -> "RpcProfile":
        """Create a default local-node profile."""
        datadir = datadir or str(default_chain_data_dir(chain_id))
        return cls(
            id=str(uuid.uuid4()),
            name=name,
            type=ProfileType.LOCAL_NODE,
            rpc_url="http://127.0.0.1:8545/rpc",
            chain_id_expected=int(chain_id),
            node_start_cmd=["animica", "node", "start"],
            node_datadir=datadir,
            node_datadir_custom=False,
            node_rpc_url="http://127.0.0.1:8545/rpc",
            explorer_base_url="",
        )
