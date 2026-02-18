"""
Registry storage backend.

Manages model manifests in local storage with optional DA/on-chain publishing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict

from .schema import ModelManifest
from .versioning import compare_versions, parse_version

logger = logging.getLogger(__name__)


class RegistryStorage:
    """
    Storage backend for model registry.
    
    Stores manifests in:
    1. Local filesystem (for development and caching)
    2. Optional: DA commitments (for production trust)
    3. Optional: On-chain pointer to latest DA commitment
    """
    
    def __init__(self, registry_dir: Path):
        """
        Initialize registry storage.
        
        Args:
            registry_dir: Directory for storing manifests
        """
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        
        self.manifests_dir = self.registry_dir / "manifests"
        self.manifests_dir.mkdir(exist_ok=True)
        
        self.pins_file = self.registry_dir / "pins.json"
        self._load_pins()
    
    def _load_pins(self):
        """Load pinned versions from storage."""
        if self.pins_file.exists():
            try:
                data = json.loads(self.pins_file.read_text())
                self.pins = data.get("pins", {})
            except Exception as e:
                logger.error(f"Failed to load pins: {e}")
                self.pins = {}
        else:
            self.pins = {}
    
    def _save_pins(self):
        """Save pinned versions to storage."""
        try:
            data = {"pins": self.pins}
            self.pins_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save pins: {e}")
    
    def save_manifest(self, manifest: ModelManifest) -> str:
        """
        Save a model manifest.
        
        Args:
            manifest: Model manifest to save
        
        Returns:
            Manifest hash
        """
        manifest_hash = manifest.compute_hash()
        
        # Save by version
        version_path = self.manifests_dir / f"{manifest.model_id}_{manifest.version}.json"
        version_path.write_text(manifest.to_json())
        
        # Save by hash (for content-addressing)
        hash_path = self.manifests_dir / f"{manifest_hash[:16]}.json"
        hash_path.write_text(manifest.to_json())
        
        logger.info(f"Saved manifest: {manifest.model_id} v{manifest.version} (hash: {manifest_hash[:16]})")
        
        return manifest_hash
    
    def load_manifest(self, model_id: str, version: str) -> Optional[ModelManifest]:
        """
        Load a model manifest by ID and version.
        
        Args:
            model_id: Model identifier
            version: Model version
        
        Returns:
            ModelManifest if found, None otherwise
        """
        version_path = self.manifests_dir / f"{model_id}_{version}.json"
        
        if not version_path.exists():
            return None
        
        try:
            json_str = version_path.read_text()
            return ModelManifest.from_json(json_str)
        except Exception as e:
            logger.error(f"Failed to load manifest {model_id} v{version}: {e}")
            return None
    
    def load_manifest_by_hash(self, manifest_hash: str) -> Optional[ModelManifest]:
        """
        Load a model manifest by hash.
        
        Args:
            manifest_hash: Manifest hash (full or prefix)
        
        Returns:
            ModelManifest if found, None otherwise
        """
        # Support both full hash and prefix
        prefix = manifest_hash[:16] if len(manifest_hash) > 16 else manifest_hash
        hash_path = self.manifests_dir / f"{prefix}.json"
        
        if not hash_path.exists():
            return None
        
        try:
            json_str = hash_path.read_text()
            return ModelManifest.from_json(json_str)
        except Exception as e:
            logger.error(f"Failed to load manifest by hash {manifest_hash}: {e}")
            return None
    
    def list_versions(self, model_id: str) -> List[str]:
        """
        List all versions for a model.
        
        Args:
            model_id: Model identifier
        
        Returns:
            List of version strings, sorted from oldest to newest
        """
        versions = []
        
        for manifest_file in self.manifests_dir.glob(f"{model_id}_*.json"):
            # Extract version from filename
            filename = manifest_file.stem
            if filename.startswith(f"{model_id}_"):
                version = filename[len(model_id) + 1:]
                versions.append(version)
        
        # Sort versions
        try:
            versions.sort(key=lambda v: parse_version(v))
        except ValueError:
            # Fallback to string sort if version parsing fails
            versions.sort()
        
        return versions
    
    def get_latest_version(self, model_id: str) -> Optional[str]:
        """
        Get the latest version for a model.
        
        Args:
            model_id: Model identifier
        
        Returns:
            Latest version string, or None if no versions exist
        """
        versions = self.list_versions(model_id)
        return versions[-1] if versions else None
    
    def pin_version(self, model_id: str, version: str) -> bool:
        """
        Pin a specific version as active.
        
        Args:
            model_id: Model identifier
            version: Version to pin
        
        Returns:
            True if successful, False otherwise
        """
        # Verify manifest exists
        manifest = self.load_manifest(model_id, version)
        if not manifest:
            logger.error(f"Cannot pin non-existent version: {model_id} v{version}")
            return False
        
        self.pins[model_id] = version
        self._save_pins()
        logger.info(f"Pinned {model_id} to v{version}")
        return True
    
    def get_pinned_version(self, model_id: str) -> Optional[str]:
        """
        Get the currently pinned version for a model.
        
        Args:
            model_id: Model identifier
        
        Returns:
            Pinned version string, or None if not pinned
        """
        return self.pins.get(model_id)
    
    def get_pinned_manifest(self, model_id: str) -> Optional[ModelManifest]:
        """
        Get the manifest for the currently pinned version.
        
        Args:
            model_id: Model identifier
        
        Returns:
            ModelManifest if pinned version exists, None otherwise
        """
        version = self.get_pinned_version(model_id)
        if not version:
            return None
        
        return self.load_manifest(model_id, version)
    
    def list_all_models(self) -> Dict[str, List[str]]:
        """
        List all models and their versions.
        
        Returns:
            Dictionary mapping model_id to list of versions
        """
        models: Dict[str, List[str]] = {}
        
        for manifest_file in self.manifests_dir.glob("*_*.json"):
            filename = manifest_file.stem
            parts = filename.split("_", 1)
            if len(parts) == 2:
                model_id, version = parts
                if model_id not in models:
                    models[model_id] = []
                if version not in models[model_id]:
                    models[model_id].append(version)
        
        # Sort versions for each model
        for model_id in models:
            try:
                models[model_id].sort(key=lambda v: parse_version(v))
            except ValueError:
                models[model_id].sort()
        
        return models
    
    def delete_version(self, model_id: str, version: str) -> bool:
        """
        Delete a specific model version.
        
        Args:
            model_id: Model identifier
            version: Version to delete
        
        Returns:
            True if successful, False otherwise
        """
        version_path = self.manifests_dir / f"{model_id}_{version}.json"
        
        if not version_path.exists():
            return False
        
        # Load manifest to get hash
        manifest = self.load_manifest(model_id, version)
        if manifest:
            manifest_hash = manifest.compute_hash()
            hash_path = self.manifests_dir / f"{manifest_hash[:16]}.json"
            if hash_path.exists():
                hash_path.unlink()
        
        # Delete version file
        version_path.unlink()
        
        # Unpin if this was the pinned version
        if self.pins.get(model_id) == version:
            del self.pins[model_id]
            self._save_pins()
        
        logger.info(f"Deleted {model_id} v{version}")
        return True
