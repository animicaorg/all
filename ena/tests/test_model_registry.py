"""Tests for model registry."""

import json
import tempfile
from pathlib import Path
import pytest

from ena.model_registry import ModelRegistry, ModelInfo


class TestModelRegistry:
    """Test model registry functionality."""
    
    def test_create_registry_with_dummy_model(self):
        """Test registry creation with dummy model."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)
            
            # Should create dummy model
            models = registry.list_models()
            assert len(models) > 0
            
            default = registry.get_default()
            assert default is not None
            assert default.name == "ena.tiny.v1"
    
    def test_get_model_by_name(self):
        """Test getting model by name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)
            
            model = registry.get_model("ena.tiny.v1")
            assert model is not None
            assert model.name == "ena.tiny.v1"
    
    def test_get_model_by_alias(self):
        """Test getting model by alias."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)
            
            model = registry.get_model("ena.latest")
            assert model is not None
            assert model.name == "ena.tiny.v1"
    
    def test_set_alias(self):
        """Test setting an alias."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)
            
            registry.set_alias("test-alias", "ena.tiny.v1")
            
            model = registry.get_model("test-alias")
            assert model is not None
            assert model.name == "ena.tiny.v1"
    
    def test_set_alias_invalid_target(self):
        """Test setting alias with invalid target."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)
            
            with pytest.raises(ValueError):
                registry.set_alias("test-alias", "nonexistent")
    
    def test_set_default(self):
        """Test setting default model."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)
            
            registry.set_default("ena.tiny.v1")
            
            default = registry.get_default()
            assert default is not None
            assert default.name == "ena.tiny.v1"
    
    def test_list_aliases(self):
        """Test listing aliases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)
            
            aliases = registry.list_aliases()
            assert "ena.latest" in aliases
            assert aliases["ena.latest"] == "ena.tiny.v1"
