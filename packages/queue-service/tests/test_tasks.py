"""
Tests for Queue Service Tasks
"""

import pytest
from queue_service.tasks.inference import process_inference, batch_inference
from queue_service.tasks.billing import charge_credits, aggregate_usage
from queue_service.tasks.github import create_pull_request


def test_process_inference_task_structure():
    """Test inference task has correct structure"""
    assert hasattr(process_inference, 'delay')
    assert hasattr(process_inference, 'apply_async')
    assert process_inference.name.endswith('process_inference')


def test_batch_inference_task_structure():
    """Test batch inference task structure"""
    assert hasattr(batch_inference, 'delay')
    assert batch_inference.name.endswith('batch_inference')


def test_charge_credits_task_structure():
    """Test charge credits task structure"""
    assert hasattr(charge_credits, 'delay')
    assert charge_credits.name.endswith('charge_credits')


def test_aggregate_usage_task_structure():
    """Test aggregate usage task structure"""
    assert hasattr(aggregate_usage, 'delay')
    assert aggregate_usage.name.endswith('aggregate_usage')


def test_create_pull_request_task_structure():
    """Test PR creation task structure"""
    assert hasattr(create_pull_request, 'delay')
    assert create_pull_request.name.endswith('create_pull_request')


def test_task_retry_configuration():
    """Test tasks have retry configuration"""
    # Inference tasks should have retry enabled
    assert process_inference.max_retries == 3
    
    # PR creation should have retry
    assert create_pull_request.max_retries == 3
