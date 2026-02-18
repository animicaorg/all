"""
Model registry for ENA LLM models.

Manages multiple model versions with aliasing support.
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """Model metadata."""
    name: str
    version: str
    path: str
    tokenizer: str
    max_tokens: int
    description: str
    created_at: str


class ModelRegistry:
    """Registry for managing multiple model versions."""
    
    def __init__(self, models_dir: str):
        self.models_dir = Path(models_dir)
        self.models: Dict[str, ModelInfo] = {}
        self.aliases: Dict[str, str] = {}
        self.default_model: Optional[str] = None
        
        # Load models from directory
        self._scan_models()
    
    def _scan_models(self):
        """Scan models directory and load model metadata."""
        if not self.models_dir.exists():
            logger.warning(f"Models directory not found: {self.models_dir}")
            self.models_dir.mkdir(parents=True, exist_ok=True)
            # Create a dummy model for testing
            self._create_dummy_model()
            return
        
        # Look for .json metadata files
        for meta_file in self.models_dir.glob("*.json"):
            try:
                with open(meta_file, "r") as f:
                    data = json.load(f)
                
                model = ModelInfo(**data)
                self.models[model.name] = model
                logger.info(f"Loaded model: {model.name} from {meta_file}")
            
            except Exception as e:
                logger.error(f"Failed to load model from {meta_file}: {e}")
        
        # If no models were loaded, create dummy model
        if not self.models:
            self._create_dummy_model()
    
    def _create_dummy_model(self):
        """Create a dummy model for testing."""
        dummy_model = ModelInfo(
            name="ena.tiny.v1",
            version="0.1.0",
            path=str(self.models_dir / "tiny.dummy"),
            tokenizer="simple",
            max_tokens=500,
            description="Dummy model for testing",
            created_at="2024-01-01T00:00:00Z",
        )
        
        # Save metadata
        meta_path = self.models_dir / "ena.tiny.v1.json"
        with open(meta_path, "w") as f:
            json.dump(asdict(dummy_model), f, indent=2)
        
        # Create dummy model file
        model_path = self.models_dir / "tiny.dummy"
        model_path.write_text("DUMMY_MODEL")
        
        self.models[dummy_model.name] = dummy_model
        self.default_model = "ena.tiny.v1"
        self.aliases["ena.latest"] = "ena.tiny.v1"
        
        logger.info("Created dummy model for testing")
    
    def get_model(self, name_or_alias: str) -> Optional[ModelInfo]:
        """
        Get model by name or alias.
        
        Args:
            name_or_alias: Model name or alias
        
        Returns:
            ModelInfo if found, None otherwise
        """
        # Check if it's an alias
        if name_or_alias in self.aliases:
            name = self.aliases[name_or_alias]
        else:
            name = name_or_alias
        
        return self.models.get(name)
    
    def list_models(self) -> List[ModelInfo]:
        """List all available models."""
        return list(self.models.values())
    
    def list_aliases(self) -> Dict[str, str]:
        """List all aliases."""
        return dict(self.aliases)
    
    def set_alias(self, alias: str, target: str):
        """
        Set an alias to point to a model.
        
        Args:
            alias: Alias name
            target: Target model name
        
        Raises:
            ValueError: If target model doesn't exist
        """
        if target not in self.models:
            raise ValueError(f"Model not found: {target}")
        
        self.aliases[alias] = target
        logger.info(f"Set alias {alias} -> {target}")
    
    def set_default(self, model_name: str):
        """
        Set the default model.
        
        Args:
            model_name: Model name
        
        Raises:
            ValueError: If model doesn't exist
        """
        if model_name not in self.models:
            raise ValueError(f"Model not found: {model_name}")
        
        self.default_model = model_name
        logger.info(f"Set default model: {model_name}")
    
    def get_default(self) -> Optional[ModelInfo]:
        """Get the default model."""
        if self.default_model:
            return self.models.get(self.default_model)
        
        # Fallback to first model
        if self.models:
            return list(self.models.values())[0]
        
        return None
    
    def reload(self):
        """Reload models from directory."""
        self.models.clear()
        self.aliases.clear()
        self._scan_models()
