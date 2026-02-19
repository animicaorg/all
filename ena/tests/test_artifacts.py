"""
Tests for artifact manifest and verification system.
"""

import pytest
from datetime import datetime

from ena.artifacts import (
    ArtifactManifest,
    ArtifactType,
    DatasetManifest,
    EvalReportManifest,
    ModelCheckpointManifest,
    RewardDataManifest,
    IndexShardManifest,
    hash_artifact,
    verify_artifact,
    ArtifactVerifier,
    VerificationResult,
)


class TestArtifactManifest:
    """Tests for base artifact manifest."""
    
    def test_create_manifest(self):
        """Test creating a basic manifest."""
        manifest = ArtifactManifest(
            artifact_id="test_id",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
        )
        
        assert manifest.artifact_id == "test_id"
        assert manifest.type == ArtifactType.DATASET_SHARD
        assert manifest.created_by == "worker1"
        assert isinstance(manifest.inputs, list)
        assert isinstance(manifest.metrics, dict)
    
    def test_to_dict(self):
        """Test conversion to dict."""
        manifest = ArtifactManifest(
            artifact_id="test_id",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            inputs=["input1", "input2"],
        )
        
        d = manifest.to_dict()
        assert d["artifact_id"] == "test_id"
        assert d["type"] == "dataset_shard"
        assert d["created_by"] == "worker1"
        assert d["inputs"] == ["input1", "input2"]
    
    def test_to_json_deterministic(self):
        """Test JSON serialization is deterministic."""
        manifest1 = ArtifactManifest(
            artifact_id="test",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            created_at="2024-01-01T00:00:00",
        )
        
        manifest2 = ArtifactManifest(
            artifact_id="test",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            created_at="2024-01-01T00:00:00",
        )
        
        # Should produce identical JSON
        assert manifest1.to_json() == manifest2.to_json()


class TestDatasetManifest:
    """Tests for dataset manifest."""
    
    def test_create_dataset_manifest(self):
        """Test creating dataset manifest."""
        manifest = DatasetManifest(
            artifact_id="dataset_001",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            source="repo:/path/to/repo",
            shard_index=0,
            total_shards=10,
            num_samples=1000,
            data_hash="abc123",
        )
        
        assert manifest.source == "repo:/path/to/repo"
        assert manifest.shard_index == 0
        assert manifest.total_shards == 10
        assert manifest.num_samples == 1000
        assert manifest.data_hash == "abc123"
    
    def test_dataset_defaults(self):
        """Test dataset manifest with defaults."""
        manifest = DatasetManifest(
            artifact_id="test",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
        )
        
        assert manifest.dedup_method == "minhash"
        assert manifest.safety_filtered is True


class TestEvalReportManifest:
    """Tests for evaluation report manifest."""
    
    def test_create_eval_report(self):
        """Test creating eval report manifest."""
        manifest = EvalReportManifest(
            artifact_id="eval_001",
            type=ArtifactType.EVAL_REPORT,
            created_by="worker1",
            model_hash="model_abc",
            eval_suite="ena_v1",
            total_score=85.5,
            category_scores={"code": 90.0, "reasoning": 81.0},
            num_tasks=100,
            pass_rate=0.85,
        )
        
        assert manifest.model_hash == "model_abc"
        assert manifest.total_score == 85.5
        assert manifest.num_tasks == 100
        assert manifest.pass_rate == 0.85


class TestModelCheckpointManifest:
    """Tests for model checkpoint manifest."""
    
    def test_create_checkpoint_manifest(self):
        """Test creating checkpoint manifest."""
        manifest = ModelCheckpointManifest(
            artifact_id="ckpt_001",
            type=ArtifactType.MODEL_CHECKPOINT,
            created_by="worker1",
            model_name="ena-small-v1",
            base_model="gpt2",
            training_method="SFT",
            num_parameters=124000000,
            is_delta=False,
        )
        
        assert manifest.model_name == "ena-small-v1"
        assert manifest.training_method == "SFT"
        assert manifest.num_parameters == 124000000
        assert manifest.is_delta is False


class TestHashingAndVerification:
    """Tests for hashing and verification functions."""
    
    def test_hash_artifact_deterministic(self):
        """Test artifact hashing is deterministic."""
        manifest = DatasetManifest(
            artifact_id="temp",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            created_at="2024-01-01T00:00:00",
            source="test",
            num_samples=100,
        )
        
        hash1 = hash_artifact(manifest)
        hash2 = hash_artifact(manifest)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA3-256 hex
    
    def test_hash_changes_with_content(self):
        """Test hash changes when content changes."""
        manifest1 = DatasetManifest(
            artifact_id="temp",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            created_at="2024-01-01T00:00:00",
            num_samples=100,
        )
        
        manifest2 = DatasetManifest(
            artifact_id="temp",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            created_at="2024-01-01T00:00:00",
            num_samples=200,  # Different
        )
        
        hash1 = hash_artifact(manifest1)
        hash2 = hash_artifact(manifest2)
        
        assert hash1 != hash2
    
    def test_verify_artifact_valid(self):
        """Test verifying valid artifact."""
        manifest = DatasetManifest(
            artifact_id="",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            source="test",
            shard_index=0,
            total_shards=1,
            num_samples=100,
        )
        
        # Set correct artifact_id
        manifest.artifact_id = hash_artifact(manifest)
        
        assert verify_artifact(manifest) is True
    
    def test_verify_artifact_invalid_hash(self):
        """Test verification fails with wrong hash."""
        manifest = DatasetManifest(
            artifact_id="wrong_hash",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            num_samples=100,
        )
        
        assert verify_artifact(manifest) is False
    
    def test_verify_artifact_missing_created_by(self):
        """Test verification fails with missing created_by."""
        manifest = DatasetManifest(
            artifact_id="test",
            type=ArtifactType.DATASET_SHARD,
            created_by="",  # Empty
            num_samples=100,
        )
        
        assert verify_artifact(manifest) is False
    
    def test_verify_dataset_invalid_shard_index(self):
        """Test verification fails with invalid shard index."""
        manifest = DatasetManifest(
            artifact_id="",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            shard_index=10,  # >= total_shards
            total_shards=5,
            num_samples=100,
        )
        manifest.artifact_id = hash_artifact(manifest)
        
        assert verify_artifact(manifest) is False
    
    def test_verify_eval_invalid_score(self):
        """Test verification fails with invalid score."""
        manifest = EvalReportManifest(
            artifact_id="",
            type=ArtifactType.EVAL_REPORT,
            created_by="worker1",
            total_score=150.0,  # > 100
            pass_rate=0.5,
        )
        manifest.artifact_id = hash_artifact(manifest)
        
        assert verify_artifact(manifest) is False


class TestArtifactVerifier:
    """Tests for ArtifactVerifier class."""
    
    def test_verifier_init(self):
        """Test verifier initialization."""
        verifier = ArtifactVerifier(sample_size=5)
        assert verifier.sample_size == 5
    
    def test_verify_valid_manifest(self):
        """Test verifying valid manifest."""
        manifest = DatasetManifest(
            artifact_id="",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            num_samples=10,
        )
        manifest.artifact_id = hash_artifact(manifest)
        
        verifier = ArtifactVerifier()
        result = verifier.verify(manifest, check_provenance=False)
        
        assert result.is_valid
        assert result.status.value == "valid"
        assert result.artifact_id == manifest.artifact_id
    
    def test_verify_invalid_manifest(self):
        """Test verifying invalid manifest."""
        manifest = DatasetManifest(
            artifact_id="wrong",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            num_samples=10,
        )
        
        verifier = ArtifactVerifier()
        result = verifier.verify(manifest, check_provenance=False)
        
        assert not result.is_valid
        assert result.status.value == "invalid_schema"
    
    def test_verify_with_sample_data(self):
        """Test verification with sample data."""
        manifest = DatasetManifest(
            artifact_id="",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            num_samples=100,
        )
        manifest.artifact_id = hash_artifact(manifest)
        
        # Create sample data
        data = [{"text": f"sample {i}"} for i in range(100)]
        
        verifier = ArtifactVerifier(sample_size=10, seed=42)
        result = verifier.verify(manifest, data=data, check_provenance=False)
        
        assert result.is_valid
        assert result.samples_checked == 10
    
    def test_verify_with_invalid_sample_data(self):
        """Test verification fails with invalid sample data."""
        manifest = DatasetManifest(
            artifact_id="",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            num_samples=100,
        )
        manifest.artifact_id = hash_artifact(manifest)
        
        # Invalid data (not list of dicts)
        data = ["string1", "string2", "string3"]
        
        verifier = ArtifactVerifier(sample_size=3)
        result = verifier.verify(manifest, data=data, check_provenance=False)
        
        assert not result.is_valid
        assert result.status.value == "invalid_samples"
    
    def test_verify_provenance_valid(self):
        """Test provenance verification with valid inputs."""
        manifest = DatasetManifest(
            artifact_id="",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            inputs=[
                "a" * 64,  # Valid SHA3-256 hex
                "b" * 64,
            ],
            num_samples=10,
        )
        manifest.artifact_id = hash_artifact(manifest)
        
        verifier = ArtifactVerifier()
        result = verifier.verify(manifest, check_provenance=True)
        
        assert result.is_valid
    
    def test_verify_provenance_invalid_hash(self):
        """Test provenance verification fails with invalid hash."""
        manifest = DatasetManifest(
            artifact_id="",
            type=ArtifactType.DATASET_SHARD,
            created_by="worker1",
            inputs=["invalid_hash"],  # Too short
            num_samples=10,
        )
        manifest.artifact_id = hash_artifact(manifest)
        
        verifier = ArtifactVerifier()
        result = verifier.verify(manifest, check_provenance=True)
        
        assert not result.is_valid
        assert result.status.value == "invalid_provenance"
        assert len(result.errors) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
