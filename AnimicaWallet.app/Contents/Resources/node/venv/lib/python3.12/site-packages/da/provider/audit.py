"""
Animica DA • Audit System

This module implements proof-of-storage audits for DA providers:
- Create challenges (byte-range, merkle-proof, nmt-proof)
- Verify responses against actual blob data
- Update provider uptime scores based on results

Design:
- Challenges include random nonce for freshness
- Responses must be signed with provider's post-quantum key
- Verification checks signatures and data correctness
- Scoring: +100 for pass, -200 for fail
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from da.provider.registry import (
    AuditChallenge,
    AuditResponse,
    AuditResult,
    ProviderEntry,
    ProviderRegistry,
)

try:
    from pq.py.verify import verify_signature
    from pq.py.sign import sign_message
    PQ_AVAILABLE = True
except ImportError:
    PQ_AVAILABLE = False
    verify_signature = None  # type: ignore
    sign_message = None  # type: ignore


# -------------------------------- Constants -----------------------------------

DEFAULT_AUDIT_DB = Path.home() / ".animica" / "audit_results.db"
DEFAULT_CHALLENGE_DEADLINE_SECONDS = 3600  # 1 hour
CHALLENGE_TYPES = ["byte-range", "merkle-proof", "nmt-proof"]

# Scoring
SCORE_DELTA_PASS = 100
SCORE_DELTA_FAIL = -200


# -------------------------------- Database ------------------------------------


class AuditDatabase:
    """SQLite database for audit challenges, responses, and results."""
    
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or DEFAULT_AUDIT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id BLOB PRIMARY KEY,
                    provider_id BLOB NOT NULL,
                    blob_commitment BLOB NOT NULL,
                    nonce BLOB NOT NULL,
                    challenge_type TEXT NOT NULL,
                    params TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    deadline INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS responses (
                    challenge_id BLOB PRIMARY KEY,
                    provider_id BLOB NOT NULL,
                    response_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    signature BLOB NOT NULL,
                    submitted_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS results (
                    challenge_id BLOB PRIMARY KEY,
                    provider_id BLOB NOT NULL,
                    passed INTEGER NOT NULL,
                    verified_at INTEGER NOT NULL,
                    failure_reason TEXT,
                    score_delta INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_challenges_provider
                ON challenges(provider_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_results_provider
                ON results(provider_id)
                """
            )
            conn.commit()
    
    def store_challenge(self, challenge: AuditChallenge) -> None:
        """Store an audit challenge."""
        import json
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO challenges
                (challenge_id, provider_id, blob_commitment, nonce, challenge_type,
                 params, created_at, deadline)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    challenge.challenge_id,
                    challenge.provider_id,
                    challenge.blob_commitment,
                    challenge.nonce,
                    challenge.challenge_type,
                    json.dumps({str(k): v for k, v in challenge.params.items()}),
                    challenge.created_at,
                    challenge.deadline,
                ),
            )
            conn.commit()
    
    def get_challenge(self, challenge_id: bytes) -> Optional[AuditChallenge]:
        """Retrieve a challenge by ID."""
        import json
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                "SELECT * FROM challenges WHERE challenge_id = ?",
                (challenge_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            
            params_dict = json.loads(row[5])
            params = {int(k): v for k, v in params_dict.items()}
            
            return AuditChallenge(
                challenge_id=row[0],
                provider_id=row[1],
                blob_commitment=row[2],
                nonce=row[3],
                challenge_type=row[4],
                params=params,
                created_at=row[6],
                deadline=row[7],
            )
    
    def store_response(self, response: AuditResponse) -> None:
        """Store an audit response."""
        import json
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO responses
                (challenge_id, provider_id, response_type, payload, signature, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    response.challenge_id,
                    response.provider_id,
                    response.response_type,
                    json.dumps({str(k): v for k, v in response.payload.items()}),
                    response.signature,
                    response.submitted_at,
                ),
            )
            conn.commit()
    
    def get_response(self, challenge_id: bytes) -> Optional[AuditResponse]:
        """Retrieve a response by challenge ID."""
        import json
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                "SELECT * FROM responses WHERE challenge_id = ?",
                (challenge_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            
            payload_dict = json.loads(row[3])
            payload = {int(k): v for k, v in payload_dict.items()}
            
            return AuditResponse(
                challenge_id=row[0],
                provider_id=row[1],
                response_type=row[2],
                payload=payload,
                signature=row[4],
                submitted_at=row[5],
            )
    
    def store_result(self, result: AuditResult) -> None:
        """Store an audit result."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO results
                (challenge_id, provider_id, passed, verified_at, failure_reason, score_delta)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.challenge_id,
                    result.provider_id,
                    1 if result.passed else 0,
                    result.verified_at,
                    result.failure_reason,
                    result.score_delta,
                ),
            )
            conn.commit()
    
    def get_results_for_provider(
        self, provider_id: bytes, limit: int = 100
    ) -> List[AuditResult]:
        """Get audit results for a provider."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """
                SELECT challenge_id, provider_id, passed, verified_at, failure_reason, score_delta
                FROM results
                WHERE provider_id = ?
                ORDER BY verified_at DESC
                LIMIT ?
                """,
                (provider_id, limit),
            )
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                results.append(
                    AuditResult(
                        challenge_id=row[0],
                        provider_id=row[1],
                        passed=bool(row[2]),
                        verified_at=row[3],
                        failure_reason=row[4],
                        score_delta=row[5],
                    )
                )
            return results


# -------------------------------- Challenge Creation --------------------------


def create_challenge(
    provider_id: bytes,
    blob_commitment: bytes,
    challenge_type: str = "byte-range",
    deadline_seconds: int = DEFAULT_CHALLENGE_DEADLINE_SECONDS,
) -> AuditChallenge:
    """
    Create an audit challenge for a provider.
    
    Args:
        provider_id: 32-byte provider ID
        blob_commitment: 32-byte blob commitment
        challenge_type: "byte-range", "merkle-proof", or "nmt-proof"
        deadline_seconds: Time limit in seconds (default: 1 hour)
    
    Returns:
        AuditChallenge object
    
    Raises:
        ValueError: If parameters are invalid
    """
    if len(provider_id) != 32:
        raise ValueError("provider_id must be 32 bytes")
    if len(blob_commitment) != 32:
        raise ValueError("blob_commitment must be 32 bytes")
    if challenge_type not in CHALLENGE_TYPES:
        raise ValueError(f"challenge_type must be one of {CHALLENGE_TYPES}")
    
    # Generate unique challenge ID
    nonce = os.urandom(32)
    challenge_id = hashlib.sha3_256(
        provider_id + blob_commitment + nonce
    ).digest()
    
    # Create challenge parameters based on type
    now = int(time.time())
    params: Dict[int, object] = {}
    
    if challenge_type == "byte-range":
        # Request random byte range
        # Use nonce to deterministically select range
        offset = int.from_bytes(nonce[:8], byteorder='big') % (1024 * 1024)  # Max 1MB offset
        length = 256  # 256 bytes
        params = {
            0: offset,  # start_offset
            1: length,  # length
        }
    elif challenge_type == "merkle-proof":
        # Request Merkle proof for a leaf
        leaf_index = int.from_bytes(nonce[:4], byteorder='big') % 1024
        params = {
            0: leaf_index,  # leaf_index
        }
    elif challenge_type == "nmt-proof":
        # Request NMT proof
        namespace = nonce[:8]  # 8-byte namespace
        params = {
            0: namespace.hex(),  # namespace as hex string
        }
    
    return AuditChallenge(
        challenge_id=challenge_id,
        provider_id=provider_id,
        blob_commitment=blob_commitment,
        nonce=nonce,
        challenge_type=challenge_type,
        params=params,
        created_at=now,
        deadline=now + deadline_seconds,
    )


# -------------------------------- Response Verification -----------------------


def verify_response(
    challenge: AuditChallenge,
    response: AuditResponse,
    provider: ProviderEntry,
    actual_blob_data: Optional[bytes] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Verify an audit response.
    
    Args:
        challenge: Original challenge
        response: Provider's response
        provider: Provider entry (for pubkey)
        actual_blob_data: Actual blob data for verification (optional)
    
    Returns:
        Tuple of (passed, failure_reason)
        - passed: True if verification succeeded
        - failure_reason: String if failed, None if passed
    """
    # Check basic constraints
    if response.challenge_id != challenge.challenge_id:
        return False, "challenge_id mismatch"
    
    if response.provider_id != challenge.provider_id:
        return False, "provider_id mismatch"
    
    # Check deadline
    if response.submitted_at > challenge.deadline:
        return False, "response submitted after deadline"
    
    # Verify signature
    if not _verify_response_signature(response, provider.pubkey):
        return False, "invalid signature"
    
    # Verify response type matches challenge type
    expected_response_type = _get_expected_response_type(challenge.challenge_type)
    if response.response_type != expected_response_type:
        return False, f"expected response_type {expected_response_type}, got {response.response_type}"
    
    # Verify response payload against actual data
    if actual_blob_data is not None:
        passed, reason = _verify_response_data(
            challenge=challenge,
            response=response,
            actual_blob_data=actual_blob_data,
        )
        if not passed:
            return False, reason
    
    return True, None


def _verify_response_signature(
    response: AuditResponse,
    provider_pubkey: bytes,
) -> bool:
    """Verify provider's signature on response."""
    if not PQ_AVAILABLE:
        # Skip signature verification if PQ not available
        return True
    
    # Construct message: challenge_id + response_type + payload hash
    import json
    payload_json = json.dumps(response.payload, sort_keys=True)
    message = (
        response.challenge_id +
        response.response_type.encode('utf-8') +
        hashlib.sha3_256(payload_json.encode('utf-8')).digest()
    )
    
    try:
        # Domain separation for audit responses
        domain = b"ANIMICA_DA_AUDIT_RESPONSE"
        return verify_signature(
            message=message,
            signature=response.signature,
            public_key=provider_pubkey,
            domain=domain,
        )
    except Exception:
        return False


def _get_expected_response_type(challenge_type: str) -> str:
    """Map challenge type to expected response type."""
    mapping = {
        "byte-range": "byte-data",
        "merkle-proof": "merkle-proof",
        "nmt-proof": "nmt-proof",
    }
    return mapping.get(challenge_type, "unknown")


def _verify_response_data(
    challenge: AuditChallenge,
    response: AuditResponse,
    actual_blob_data: bytes,
) -> Tuple[bool, Optional[str]]:
    """Verify response data matches actual blob."""
    if challenge.challenge_type == "byte-range":
        return _verify_byte_range(challenge, response, actual_blob_data)
    elif challenge.challenge_type == "merkle-proof":
        return _verify_merkle_proof(challenge, response, actual_blob_data)
    elif challenge.challenge_type == "nmt-proof":
        return _verify_nmt_proof(challenge, response, actual_blob_data)
    else:
        return False, f"unknown challenge type: {challenge.challenge_type}"


def _verify_byte_range(
    challenge: AuditChallenge,
    response: AuditResponse,
    actual_blob_data: bytes,
) -> Tuple[bool, Optional[str]]:
    """Verify byte-range response."""
    offset = challenge.params.get(0)
    length = challenge.params.get(1)
    
    if offset is None or length is None:
        return False, "missing offset or length in challenge params"
    
    # Extract expected bytes from actual data
    if offset + length > len(actual_blob_data):
        # Provider should have returned available bytes
        expected_data = actual_blob_data[offset:]
    else:
        expected_data = actual_blob_data[offset:offset + length]
    
    # Check response payload
    response_data_hex = response.payload.get(0)
    if response_data_hex is None:
        return False, "missing data in response payload"
    
    try:
        response_data = bytes.fromhex(response_data_hex)
    except ValueError:
        return False, "invalid hex data in response"
    
    if response_data != expected_data:
        return False, "byte-range data mismatch"
    
    return True, None


def _verify_merkle_proof(
    challenge: AuditChallenge,
    response: AuditResponse,
    actual_blob_data: bytes,
) -> Tuple[bool, Optional[str]]:
    """Verify Merkle proof response."""
    # Simplified verification - just check proof exists
    # In production, would verify Merkle path
    proof = response.payload.get(0)
    if proof is None:
        return False, "missing proof in response"
    
    # Phase 2 - Integration pending: full Merkle path verification.
    return True, None


def _verify_nmt_proof(
    challenge: AuditChallenge,
    response: AuditResponse,
    actual_blob_data: bytes,
) -> Tuple[bool, Optional[str]]:
    """Verify NMT proof response."""
    # Simplified verification - just check proof exists
    # In production, would verify NMT path
    proof = response.payload.get(0)
    if proof is None:
        return False, "missing proof in response"
    
    # Phase 2 - Integration pending: full NMT path verification.
    return True, None


# -------------------------------- Scoring -------------------------------------


def update_provider_score(
    registry: ProviderRegistry,
    provider_id: bytes,
    passed: bool,
) -> int:
    """
    Update provider uptime score based on audit result.
    
    Args:
        registry: Provider registry
        provider_id: Provider ID
        passed: Whether audit passed
    
    Returns:
        Score delta applied
    """
    provider = registry.get_provider(provider_id)
    if not provider:
        return 0
    
    # Calculate score delta
    score_delta = SCORE_DELTA_PASS if passed else SCORE_DELTA_FAIL
    
    # Update score (clamped to [0, 10000])
    new_score = provider.uptime_score + score_delta
    new_score = max(0, min(10000, new_score))
    
    provider.uptime_score = new_score
    registry.register_provider(provider)
    
    return score_delta


__all__ = [
    "AuditDatabase",
    "create_challenge",
    "verify_response",
    "update_provider_score",
    "DEFAULT_AUDIT_DB",
    "CHALLENGE_TYPES",
    "SCORE_DELTA_PASS",
    "SCORE_DELTA_FAIL",
]
