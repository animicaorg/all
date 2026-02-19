"""
Animica DA • Provider Registry

This module implements the storage provider registry for the DA layer,
managing provider registration, blob assignments, and audit challenges.

Follows the schema in da/schemas/provider_registry.cddl.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import cbor2  # type: ignore
except ImportError:
    cbor2 = None  # type: ignore

import json


# -------------------------------- Constants -----------------------------------

DEFAULT_REGISTRY_DB = Path.home() / ".animica" / "provider_registry.db"
DEFAULT_REPLICATION_FACTOR = 3
DEFAULT_UPTIME_SCORE = 5000  # 50%
MAX_UPTIME_SCORE = 10000  # 100%


# -------------------------------- Dataclasses ---------------------------------


@dataclass
class ProviderEntry:
    """
    Provider registration entry matching provider_entry in CDDL schema.
    """

    # Identity
    provider_id: bytes  # 32-byte SHA3-256 hash of pubkey
    pubkey: bytes  # Post-quantum public key (Dilithium3)
    address: bytes  # 20-byte payment address

    # Service info
    endpoint: Optional[str]  # HTTP(S) endpoint for serving blobs
    capacity_bytes_advertised: int  # Total capacity advertised
    capacity_bytes_committed: int  # Currently assigned/used

    # Economics
    pricing: Optional[Dict[str, int]] = None  # Optional pricing info

    # Metadata
    region_tags: List[str] = field(default_factory=list)
    uptime_score: int = DEFAULT_UPTIME_SCORE  # 0-10000
    last_heartbeat: int = 0  # Unix timestamp
    registered_at: int = 0  # Unix timestamp

    # Status
    active: bool = True
    jailed_until: Optional[int] = None  # Unix timestamp if jailed
    notes: Optional[str] = None

    def to_cbor_dict(self) -> Dict[int, object]:
        """Convert to CBOR-encodable dict matching CDDL schema."""
        d: Dict[int, object] = {
            0: self.provider_id,
            1: self.pubkey,
            2: self.address,
            3: self.endpoint,
            4: self.capacity_bytes_advertised,
            5: self.capacity_bytes_committed,
            7: self.region_tags,
            8: self.uptime_score,
            9: self.last_heartbeat,
            10: self.registered_at,
            11: self.active,
        }
        if self.pricing is not None:
            d[6] = self.pricing
        if self.jailed_until is not None:
            d[12] = self.jailed_until
        if self.notes is not None:
            d[13] = self.notes
        return d

    @classmethod
    def from_cbor_dict(cls, d: Dict[int, object]) -> ProviderEntry:
        """Reconstruct from CBOR dict."""
        return cls(
            provider_id=d[0],  # type: ignore
            pubkey=d[1],  # type: ignore
            address=d[2],  # type: ignore
            endpoint=d[3],  # type: ignore
            capacity_bytes_advertised=d[4],  # type: ignore
            capacity_bytes_committed=d[5],  # type: ignore
            pricing=d.get(6),  # type: ignore
            region_tags=d.get(7, []),  # type: ignore
            uptime_score=d.get(8, DEFAULT_UPTIME_SCORE),  # type: ignore
            last_heartbeat=d.get(9, 0),  # type: ignore
            registered_at=d.get(10, 0),  # type: ignore
            active=d.get(11, True),  # type: ignore
            jailed_until=d.get(12),  # type: ignore
            notes=d.get(13),  # type: ignore
        )

    def validate(self) -> None:
        """Validate constraints from CDDL schema."""
        if len(self.provider_id) != 32:
            raise ValueError("provider_id must be 32 bytes")
        if len(self.address) != 20:
            raise ValueError("address must be 20 bytes")
        if self.capacity_bytes_committed > self.capacity_bytes_advertised:
            raise ValueError("committed capacity exceeds advertised capacity")
        if not (0 <= self.uptime_score <= MAX_UPTIME_SCORE):
            raise ValueError(f"uptime_score must be 0-{MAX_UPTIME_SCORE}")
        if self.jailed_until is not None and self.active:
            raise ValueError("jailed providers must have active=False")


@dataclass
class BlobAssignment:
    """
    Blob assignment to a provider matching blob_assignment in CDDL schema.
    """

    blob_commitment: bytes  # 32-byte commitment
    provider_id: bytes  # 32-byte provider ID
    assigned_at: int  # Unix timestamp
    replicas: int  # Replication factor
    blob_size: int  # Size in bytes

    def to_cbor_dict(self) -> Dict[int, object]:
        """Convert to CBOR-encodable dict."""
        return {
            0: self.blob_commitment,
            1: self.provider_id,
            2: self.assigned_at,
            3: self.replicas,
            4: self.blob_size,
        }

    @classmethod
    def from_cbor_dict(cls, d: Dict[int, object]) -> BlobAssignment:
        """Reconstruct from CBOR dict."""
        return cls(
            blob_commitment=d[0],  # type: ignore
            provider_id=d[1],  # type: ignore
            assigned_at=d[2],  # type: ignore
            replicas=d[3],  # type: ignore
            blob_size=d[4],  # type: ignore
        )


@dataclass
class AuditChallenge:
    """
    Challenge sent to provider to prove storage.
    """

    challenge_id: bytes  # 32-byte unique identifier
    provider_id: bytes  # 32-byte provider ID
    blob_commitment: bytes  # 32-byte blob commitment
    nonce: bytes  # 32-byte random nonce
    challenge_type: str  # "byte-range", "merkle-proof", or "nmt-proof"
    params: Dict[int, object]  # Challenge-specific parameters
    created_at: int  # Unix timestamp
    deadline: int  # Unix timestamp

    def to_cbor_dict(self) -> Dict[int, object]:
        """Convert to CBOR-encodable dict."""
        return {
            0: self.challenge_id,
            1: self.provider_id,
            2: self.blob_commitment,
            3: self.nonce,
            4: self.challenge_type,
            5: self.params,
            6: self.created_at,
            7: self.deadline,
        }

    @classmethod
    def from_cbor_dict(cls, d: Dict[int, object]) -> AuditChallenge:
        """Reconstruct from CBOR dict."""
        return cls(
            challenge_id=d[0],  # type: ignore
            provider_id=d[1],  # type: ignore
            blob_commitment=d[2],  # type: ignore
            nonce=d[3],  # type: ignore
            challenge_type=d[4],  # type: ignore
            params=d[5],  # type: ignore
            created_at=d[6],  # type: ignore
            deadline=d[7],  # type: ignore
        )


@dataclass
class AuditResponse:
    """
    Response from provider to audit challenge.
    """

    challenge_id: bytes  # 32-byte challenge ID
    provider_id: bytes  # 32-byte provider ID
    response_type: str  # "byte-data", "merkle-proof", or "nmt-proof"
    payload: Dict[int, object]  # Response-specific payload
    signature: bytes  # Provider signature
    submitted_at: int  # Unix timestamp

    def to_cbor_dict(self) -> Dict[int, object]:
        """Convert to CBOR-encodable dict."""
        return {
            0: self.challenge_id,
            1: self.provider_id,
            2: self.response_type,
            3: self.payload,
            4: self.signature,
            5: self.submitted_at,
        }

    @classmethod
    def from_cbor_dict(cls, d: Dict[int, object]) -> AuditResponse:
        """Reconstruct from CBOR dict."""
        return cls(
            challenge_id=d[0],  # type: ignore
            provider_id=d[1],  # type: ignore
            response_type=d[2],  # type: ignore
            payload=d[3],  # type: ignore
            signature=d[4],  # type: ignore
            submitted_at=d[5],  # type: ignore
        )


@dataclass
class AuditResult:
    """
    Audit verification result.
    """

    challenge_id: bytes  # 32-byte challenge ID
    provider_id: bytes  # 32-byte provider ID
    passed: bool  # Verification passed
    verified_at: int  # Unix timestamp
    failure_reason: Optional[str] = None  # If failed, why
    score_delta: int = 0  # Change to uptime_score (+/-)

    def to_cbor_dict(self) -> Dict[int, object]:
        """Convert to CBOR-encodable dict."""
        d: Dict[int, object] = {
            0: self.challenge_id,
            1: self.provider_id,
            2: self.passed,
            3: self.verified_at,
            5: self.score_delta,
        }
        if self.failure_reason is not None:
            d[4] = self.failure_reason
        return d

    @classmethod
    def from_cbor_dict(cls, d: Dict[int, object]) -> AuditResult:
        """Reconstruct from CBOR dict."""
        return cls(
            challenge_id=d[0],  # type: ignore
            provider_id=d[1],  # type: ignore
            passed=d[2],  # type: ignore
            verified_at=d[3],  # type: ignore
            failure_reason=d.get(4),  # type: ignore
            score_delta=d.get(5, 0),  # type: ignore
        )


# -------------------------------- Registry ------------------------------------


class ProviderRegistry:
    """
    Provider registry with SQLite persistence.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or DEFAULT_REGISTRY_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS providers (
                    provider_id BLOB PRIMARY KEY,
                    pubkey BLOB NOT NULL,
                    address BLOB NOT NULL,
                    endpoint TEXT,
                    capacity_bytes_advertised INTEGER NOT NULL,
                    capacity_bytes_committed INTEGER NOT NULL,
                    pricing TEXT,
                    region_tags TEXT,
                    uptime_score INTEGER NOT NULL,
                    last_heartbeat INTEGER NOT NULL,
                    registered_at INTEGER NOT NULL,
                    active INTEGER NOT NULL,
                    jailed_until INTEGER,
                    notes TEXT,
                    cbor_data BLOB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blob_assignments (
                    blob_commitment BLOB NOT NULL,
                    provider_id BLOB NOT NULL,
                    assigned_at INTEGER NOT NULL,
                    replicas INTEGER NOT NULL,
                    blob_size INTEGER NOT NULL,
                    cbor_data BLOB NOT NULL,
                    PRIMARY KEY (blob_commitment, provider_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_assignments_provider
                ON blob_assignments(provider_id)
                """
            )
            conn.commit()

    def _encode_entry(self, entry: ProviderEntry) -> bytes:
        """Encode provider entry to bytes (CBOR if available, else JSON)."""
        if cbor2:
            return cbor2.dumps(entry.to_cbor_dict())
        else:
            # Fallback to JSON with hex-encoded bytes
            d = entry.to_cbor_dict()
            # Convert bytes to hex strings for JSON
            json_d = {}
            for k, v in d.items():
                if isinstance(v, bytes):
                    json_d[k] = v.hex()
                elif isinstance(v, list) and v and isinstance(v[0], bytes):
                    json_d[k] = [x.hex() if isinstance(x, bytes) else x for x in v]
                else:
                    json_d[k] = v
            return json.dumps(json_d).encode('utf-8')
    
    def _decode_entry(self, data: bytes) -> ProviderEntry:
        """Decode provider entry from bytes."""
        if cbor2:
            d = cbor2.loads(data)
            return ProviderEntry.from_cbor_dict(d)
        else:
            # Fallback from JSON
            json_d = json.loads(data.decode('utf-8'))
            # Convert hex strings back to bytes
            d = {}
            for k, v in json_d.items():
                k_int = int(k)
                if k_int in (0, 1, 2):  # provider_id, pubkey, address
                    d[k_int] = bytes.fromhex(v) if isinstance(v, str) else v
                else:
                    d[k_int] = v
            return ProviderEntry.from_cbor_dict(d)
    
    def _encode_assignment(self, assignment: BlobAssignment) -> bytes:
        """Encode blob assignment to bytes."""
        if cbor2:
            return cbor2.dumps(assignment.to_cbor_dict())
        else:
            d = assignment.to_cbor_dict()
            json_d = {}
            for k, v in d.items():
                if isinstance(v, bytes):
                    json_d[k] = v.hex()
                else:
                    json_d[k] = v
            return json.dumps(json_d).encode('utf-8')
    
    def _decode_assignment(self, data: bytes) -> BlobAssignment:
        """Decode blob assignment from bytes."""
        if cbor2:
            d = cbor2.loads(data)
            return BlobAssignment.from_cbor_dict(d)
        else:
            json_d = json.loads(data.decode('utf-8'))
            d = {}
            for k, v in json_d.items():
                k_int = int(k)
                if k_int in (0, 1):  # blob_commitment, provider_id
                    d[k_int] = bytes.fromhex(v) if isinstance(v, str) else v
                else:
                    d[k_int] = v
            return BlobAssignment.from_cbor_dict(d)
    
    def register_provider(self, entry: ProviderEntry) -> None:
        """Register or update a provider."""
        entry.validate()
        cbor_data = self._encode_entry(entry)

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO providers (
                    provider_id, pubkey, address, endpoint,
                    capacity_bytes_advertised, capacity_bytes_committed,
                    pricing, region_tags, uptime_score, last_heartbeat,
                    registered_at, active, jailed_until, notes, cbor_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.provider_id,
                    entry.pubkey,
                    entry.address,
                    entry.endpoint,
                    entry.capacity_bytes_advertised,
                    entry.capacity_bytes_committed,
                    str(entry.pricing) if entry.pricing else None,
                    ",".join(entry.region_tags),
                    entry.uptime_score,
                    entry.last_heartbeat,
                    entry.registered_at,
                    1 if entry.active else 0,
                    entry.jailed_until,
                    entry.notes,
                    cbor_data,
                ),
            )
            conn.commit()

    def get_provider(self, provider_id: bytes) -> Optional[ProviderEntry]:
        """Retrieve a provider by ID."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                "SELECT cbor_data FROM providers WHERE provider_id = ?",
                (provider_id,),
            )
            row = cursor.fetchone()
            if row is None or not row[0]:
                return None
            return self._decode_entry(row[0])

    def list_providers(
        self, active_only: bool = False
    ) -> List[Tuple[bytes, ProviderEntry]]:
        """List all providers."""
        query = "SELECT provider_id, cbor_data FROM providers"
        if active_only:
            query += " WHERE active = 1"

        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(query)
            rows = cursor.fetchall()
            result = []
            for provider_id, cbor_data in rows:
                if cbor_data:
                    entry = self._decode_entry(cbor_data)
                    result.append((provider_id, entry))
            return result

    def update_heartbeat(self, provider_id: bytes, timestamp: int) -> None:
        """Update last_heartbeat for a provider."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE providers SET last_heartbeat = ? WHERE provider_id = ?",
                (timestamp, provider_id),
            )
            conn.commit()

    def add_assignment(self, assignment: BlobAssignment) -> None:
        """Add a blob assignment."""
        cbor_data = self._encode_assignment(assignment)

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO blob_assignments (
                    blob_commitment, provider_id, assigned_at,
                    replicas, blob_size, cbor_data
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment.blob_commitment,
                    assignment.provider_id,
                    assignment.assigned_at,
                    assignment.replicas,
                    assignment.blob_size,
                    cbor_data,
                ),
            )
            conn.commit()

    def get_assignments_for_provider(
        self, provider_id: bytes
    ) -> List[BlobAssignment]:
        """Get all blob assignments for a provider."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                "SELECT cbor_data FROM blob_assignments WHERE provider_id = ?",
                (provider_id,),
            )
            rows = cursor.fetchall()
            result = []
            for (cbor_data,) in rows:
                if cbor_data:
                    result.append(self._decode_assignment(cbor_data))
            return result

    def get_total_capacity(self) -> Tuple[int, int]:
        """Get total advertised and committed capacity across all providers."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """
                SELECT 
                    COALESCE(SUM(capacity_bytes_advertised), 0),
                    COALESCE(SUM(capacity_bytes_committed), 0)
                FROM providers WHERE active = 1
                """
            )
            row = cursor.fetchone()
            return (row[0], row[1]) if row else (0, 0)


# -------------------------------- Helpers -------------------------------------


def create_provider_id(pubkey: bytes) -> bytes:
    """Create provider_id as SHA3-256 hash of pubkey."""
    return hashlib.sha3_256(pubkey).digest()


def create_provider_entry(
    pubkey: bytes,
    address: bytes,
    endpoint: Optional[str],
    capacity_bytes: int,
    region_tags: Optional[List[str]] = None,
) -> ProviderEntry:
    """Helper to create a new provider entry."""
    provider_id = create_provider_id(pubkey)
    now = int(time.time())

    return ProviderEntry(
        provider_id=provider_id,
        pubkey=pubkey,
        address=address,
        endpoint=endpoint,
        capacity_bytes_advertised=capacity_bytes,
        capacity_bytes_committed=0,
        region_tags=region_tags or [],
        uptime_score=DEFAULT_UPTIME_SCORE,
        last_heartbeat=now,
        registered_at=now,
        active=True,
    )


def register_provider(
    registry: ProviderRegistry,
    pubkey: bytes,
    address: bytes,
    endpoint: Optional[str],
    capacity_bytes: int,
    region_tags: Optional[List[str]] = None,
) -> ProviderEntry:
    """Helper to register a provider."""
    entry = create_provider_entry(
        pubkey=pubkey,
        address=address,
        endpoint=endpoint,
        capacity_bytes=capacity_bytes,
        region_tags=region_tags,
    )
    registry.register_provider(entry)
    return entry


__all__ = [
    "ProviderEntry",
    "BlobAssignment",
    "AuditChallenge",
    "AuditResponse",
    "AuditResult",
    "ProviderRegistry",
    "create_provider_id",
    "create_provider_entry",
    "register_provider",
    "DEFAULT_REGISTRY_DB",
    "DEFAULT_REPLICATION_FACTOR",
    "DEFAULT_UPTIME_SCORE",
    "MAX_UPTIME_SCORE",
]
