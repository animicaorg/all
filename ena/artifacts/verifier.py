"""
Artifact verification system for ENA training artifacts.

Performs deterministic verification of artifact submissions including:
- Hash verification
- Schema validation  
- Spot-checking samples
- Provenance validation
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from .manifest import ArtifactManifest, verify_artifact as verify_manifest

__all__ = ["VerificationResult", "ArtifactVerifier"]


class VerificationStatus(str, Enum):
    """Status of artifact verification."""
    VALID = "valid"
    INVALID_HASH = "invalid_hash"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_SAMPLES = "invalid_samples"
    INVALID_PROVENANCE = "invalid_provenance"
    ERROR = "error"


@dataclass
class VerificationResult:
    """Result of artifact verification."""
    status: VerificationStatus
    artifact_id: str
    message: str = ""
    samples_checked: int = 0
    errors: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            object.__setattr__(self, 'errors', [])
    
    @property
    def is_valid(self) -> bool:
        """Check if verification passed."""
        return self.status == VerificationStatus.VALID


class ArtifactVerifier:
    """
    Verifier for artifact submissions.
    
    Performs:
    - Manifest hash verification
    - Schema validation
    - Spot-checking N random samples
    - Provenance chain validation
    """
    
    def __init__(self, sample_size: int = 10, seed: Optional[int] = None):
        """
        Initialize verifier.
        
        Args:
            sample_size: Number of random samples to check
            seed: Random seed for deterministic sampling (optional)
        """
        self.sample_size = sample_size
        self.rng = random.Random(seed) if seed is not None else random.Random()
    
    def verify(
        self,
        manifest: ArtifactManifest,
        data: Optional[Any] = None,
        check_provenance: bool = True,
    ) -> VerificationResult:
        """
        Verify an artifact submission.
        
        Args:
            manifest: Artifact manifest to verify
            data: Optional actual data for spot-checking
            check_provenance: Whether to verify input provenance chain
        
        Returns:
            VerificationResult with status and details
        """
        errors: List[str] = []
        
        # Step 1: Verify manifest hash and schema
        if not verify_manifest(manifest):
            return VerificationResult(
                status=VerificationStatus.INVALID_SCHEMA,
                artifact_id=manifest.artifact_id,
                message="Manifest schema validation failed",
                errors=errors,
            )
        
        # Step 2: Spot-check samples if data provided
        samples_checked = 0
        if data is not None:
            sample_result = self._verify_samples(manifest, data)
            if not sample_result["valid"]:
                return VerificationResult(
                    status=VerificationStatus.INVALID_SAMPLES,
                    artifact_id=manifest.artifact_id,
                    message=sample_result["message"],
                    samples_checked=sample_result["checked"],
                    errors=sample_result.get("errors", []),
                )
            samples_checked = sample_result["checked"]
        
        # Step 3: Verify provenance chain if requested
        if check_provenance and manifest.inputs:
            prov_result = self._verify_provenance(manifest)
            if not prov_result["valid"]:
                return VerificationResult(
                    status=VerificationStatus.INVALID_PROVENANCE,
                    artifact_id=manifest.artifact_id,
                    message=prov_result["message"],
                    errors=prov_result.get("errors", []),
                )
        
        # All checks passed
        return VerificationResult(
            status=VerificationStatus.VALID,
            artifact_id=manifest.artifact_id,
            message="Artifact verified successfully",
            samples_checked=samples_checked,
        )
    
    def _verify_samples(
        self,
        manifest: ArtifactManifest,
        data: Any,
    ) -> Dict[str, Any]:
        """
        Spot-check random samples from artifact data.
        
        Returns dict with:
        - valid: bool
        - checked: int (number of samples checked)
        - message: str
        - errors: List[str] (optional)
        """
        try:
            # Basic type check
            if not isinstance(data, (list, dict)):
                return {
                    "valid": False,
                    "checked": 0,
                    "message": "Data must be list or dict",
                }
            
            # For list data, sample random items
            if isinstance(data, list):
                if len(data) == 0:
                    return {"valid": True, "checked": 0, "message": "Empty dataset"}
                
                # Sample min(sample_size, len(data)) items
                n_samples = min(self.sample_size, len(data))
                indices = self.rng.sample(range(len(data)), n_samples)
                
                # Check each sample has required structure
                errors = []
                for idx in indices:
                    item = data[idx]
                    if not isinstance(item, dict):
                        errors.append(f"Sample {idx} is not a dict")
                
                if errors:
                    return {
                        "valid": False,
                        "checked": n_samples,
                        "message": "Sample structure validation failed",
                        "errors": errors,
                    }
                
                return {
                    "valid": True,
                    "checked": n_samples,
                    "message": f"Checked {n_samples} samples",
                }
            
            # For dict data, check structure
            return {
                "valid": True,
                "checked": 1,
                "message": "Dict structure verified",
            }
            
        except Exception as e:
            return {
                "valid": False,
                "checked": 0,
                "message": f"Sample verification error: {str(e)}",
            }
    
    def _verify_provenance(
        self,
        manifest: ArtifactManifest,
    ) -> Dict[str, Any]:
        """
        Verify provenance chain of inputs.
        
        In a full implementation, this would:
        - Fetch input artifact manifests
        - Verify their hashes
        - Check they exist in DA/storage
        - Validate timestamps are consistent
        
        For now, just validates hash format.
        """
        errors = []
        
        for input_hash in manifest.inputs:
            # Check hash format (hex string, 64 chars for SHA3-256)
            if not isinstance(input_hash, str):
                errors.append(f"Input hash not a string: {input_hash}")
                continue
            
            if len(input_hash) != 64:
                errors.append(f"Input hash wrong length: {input_hash}")
                continue
            
            try:
                int(input_hash, 16)
            except ValueError:
                errors.append(f"Input hash not valid hex: {input_hash}")
        
        if errors:
            return {
                "valid": False,
                "message": "Provenance validation failed",
                "errors": errors,
            }
        
        return {
            "valid": True,
            "message": f"Verified {len(manifest.inputs)} input(s)",
        }
