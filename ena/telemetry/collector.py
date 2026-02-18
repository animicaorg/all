"""
Telemetry data collector.

Collects training examples from ENA usage with aggressive redaction
to protect user privacy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from .config import load_telemetry_config

logger = logging.getLogger(__name__)


@dataclass
class RedactionPolicy:
    """
    Policy for redacting sensitive data.
    
    Better safe than sorry - aggressive redaction by default.
    """
    redact_emails: bool = True
    redact_long_numbers: bool = True  # Numbers > 10 digits
    redact_api_keys: bool = True
    redact_urls: bool = False
    redact_code_blocks: bool = False  # Code is usually safe
    
    # Patterns
    email_pattern: str = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    long_number_pattern: str = r'\b\d{11,}\b'  # 11+ digits
    api_key_pattern: str = r'\b[A-Za-z0-9_-]{32,}\b'  # Long alphanumeric strings
    url_pattern: str = r'https?://[^\s]+'


@dataclass
class TelemetrySample:
    """A single training example collected from ENA usage."""
    sample_id: str  # Unique ID
    timestamp: str  # ISO8601
    
    # The interaction
    prompt: str
    response: str
    
    # Metadata (redacted)
    user_id_hash: str  # Hashed, never raw
    model_version: str
    
    # Quality signals
    feedback_score: Optional[float] = None  # 0.0 to 1.0
    edited_response: Optional[str] = None  # User edit (implies low quality)
    flagged: bool = False  # Flagged for review
    
    # Redaction metadata
    redacted: bool = False
    redaction_count: int = 0
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON."""
        return json.dumps(self.to_dict(), indent=2)


class TelemetryCollector:
    """
    Collector for ENA telemetry data.
    
    Privacy-first design:
    - Only collects if opt_in=True
    - Aggressive redaction
    - Local buffer with user control
    - Never auto-uploads without consent
    """
    
    def __init__(
        self,
        buffer_dir: Optional[Path] = None,
        redaction_policy: Optional[RedactionPolicy] = None,
    ):
        """
        Initialize collector.
        
        Args:
            buffer_dir: Directory for local buffer (default: ~/.animica/telemetry_buffer)
            redaction_policy: Redaction policy (default: RedactionPolicy())
        """
        self.config = load_telemetry_config()
        
        if buffer_dir is None:
            self.buffer_dir = Path.home() / ".animica" / "telemetry_buffer"
        else:
            self.buffer_dir = buffer_dir
        
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        
        self.redaction_policy = redaction_policy or RedactionPolicy()
        
        logger.info(f"Telemetry collector initialized: buffer_dir={self.buffer_dir}, opt_in={self.config.opt_in}")
    
    def collect(
        self,
        prompt: str,
        response: str,
        model_version: str,
        feedback_score: Optional[float] = None,
        edited_response: Optional[str] = None,
    ) -> Optional[str]:
        """
        Collect a training example.
        
        Args:
            prompt: User prompt
            response: Model response
            model_version: Model version
            feedback_score: Optional feedback score (0.0 to 1.0)
            edited_response: Optional user edit
            
        Returns:
            Sample ID if collected, None if telemetry disabled
        """
        # Check if telemetry is enabled
        if not self.config.opt_in:
            return None
        
        # Check buffer size
        if self._get_buffer_size() >= self.config.max_buffer_size:
            logger.warning(f"Buffer full ({self.config.max_buffer_size} samples), skipping collection")
            return None
        
        # Redact sensitive data
        redacted_prompt, prompt_count = self._redact(prompt)
        redacted_response, response_count = self._redact(response)
        redaction_count = prompt_count + response_count
        
        # Create sample
        sample_id = self._generate_sample_id(prompt, response)
        sample = TelemetrySample(
            sample_id=sample_id,
            timestamp=datetime.utcnow().isoformat(),
            prompt=redacted_prompt,
            response=redacted_response,
            user_id_hash=self.config.user_id_hash or "unknown",
            model_version=model_version,
            feedback_score=feedback_score,
            edited_response=self._redact(edited_response)[0] if edited_response else None,
            redacted=redaction_count > 0,
            redaction_count=redaction_count,
        )
        
        # Save to buffer
        self._save_sample(sample)
        
        logger.info(f"Sample collected: {sample_id}, redactions={redaction_count}")
        return sample_id
    
    def inspect(self, limit: int = 10) -> List[TelemetrySample]:
        """
        Inspect samples in buffer.
        
        Args:
            limit: Max samples to return
            
        Returns:
            List of samples
        """
        samples = []
        sample_files = sorted(self.buffer_dir.glob("*.json"))[:limit]
        
        for sample_file in sample_files:
            try:
                with open(sample_file, 'r') as f:
                    data = json.load(f)
                sample = TelemetrySample(**data)
                samples.append(sample)
            except Exception as e:
                logger.warning(f"Failed to load sample {sample_file}: {e}")
        
        return samples
    
    def delete(self, sample_id: Optional[str] = None) -> int:
        """
        Delete sample(s) from buffer.
        
        Args:
            sample_id: Sample ID to delete, or None to delete all
            
        Returns:
            Number of samples deleted
        """
        if sample_id:
            # Delete specific sample
            sample_file = self.buffer_dir / f"{sample_id}.json"
            if sample_file.exists():
                sample_file.unlink()
                logger.info(f"Sample deleted: {sample_id}")
                return 1
            else:
                logger.warning(f"Sample not found: {sample_id}")
                return 0
        else:
            # Delete all samples
            sample_files = list(self.buffer_dir.glob("*.json"))
            count = len(sample_files)
            for sample_file in sample_files:
                sample_file.unlink()
            logger.info(f"All samples deleted: {count}")
            return count
    
    def get_buffer_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the buffer.
        
        Returns:
            Dict with buffer stats
        """
        sample_files = list(self.buffer_dir.glob("*.json"))
        
        stats = {
            "total_samples": len(sample_files),
            "max_buffer_size": self.config.max_buffer_size,
            "buffer_full": len(sample_files) >= self.config.max_buffer_size,
            "opt_in": self.config.opt_in,
        }
        
        # Calculate total size
        total_size = sum(f.stat().st_size for f in sample_files)
        stats["total_size_mb"] = total_size / (1024 ** 2)
        
        return stats
    
    # ---- Internal Methods ----
    
    def _redact(self, text: Optional[str]) -> tuple[str, int]:
        """
        Redact sensitive data from text.
        
        Args:
            text: Text to redact
            
        Returns:
            (redacted_text, redaction_count)
        """
        if text is None:
            return None, 0
        
        redaction_count = 0
        
        # Redact emails
        if self.redaction_policy.redact_emails:
            text, count = re.subn(
                self.redaction_policy.email_pattern,
                "[EMAIL_REDACTED]",
                text
            )
            redaction_count += count
        
        # Redact long numbers (phone, CC, SSN, etc.)
        if self.redaction_policy.redact_long_numbers:
            text, count = re.subn(
                self.redaction_policy.long_number_pattern,
                "[NUMBER_REDACTED]",
                text
            )
            redaction_count += count
        
        # Redact API keys
        if self.redaction_policy.redact_api_keys:
            # Look for common API key patterns
            text, count = re.subn(
                r'\b[A-Za-z0-9_-]{32,}\b',
                "[KEY_REDACTED]",
                text
            )
            redaction_count += count
        
        # Redact URLs (optional)
        if self.redaction_policy.redact_urls:
            text, count = re.subn(
                self.redaction_policy.url_pattern,
                "[URL_REDACTED]",
                text
            )
            redaction_count += count
        
        return text, redaction_count
    
    def _generate_sample_id(self, prompt: str, response: str) -> str:
        """Generate unique sample ID."""
        content = f"{prompt}|{response}|{datetime.utcnow().isoformat()}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]
    
    def _save_sample(self, sample: TelemetrySample) -> None:
        """Save sample to buffer."""
        sample_file = self.buffer_dir / f"{sample.sample_id}.json"
        with open(sample_file, 'w') as f:
            json.dump(sample.to_dict(), f, indent=2)
    
    def _get_buffer_size(self) -> int:
        """Get current buffer size."""
        return len(list(self.buffer_dir.glob("*.json")))
