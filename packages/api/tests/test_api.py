"""Tests for API Gateway"""
import pytest
from fastapi.testclient import TestClient
from api.main import app


@pytest.fixture
def client():
    """Test client fixture"""
    return TestClient(app)


def test_root_endpoint(client):
    """Test root endpoint returns app info"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data


def test_health_check(client):
    """Test health check endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


def test_models_list(client):
    """Test models list endpoint"""
    response = client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    assert len(data["data"]) > 0


def test_chat_completion_non_streaming(client):
    """Test chat completion without streaming"""
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "llama-3-8b-instruct",
            "messages": [
                {"role": "user", "content": "Hello"}
            ],
            "stream": False
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert len(data["choices"]) > 0


def test_authentication_register(client):
    """Test user registration"""
    response = client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "secure_password"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_code_execution(client):
    """Test code execution endpoint"""
    response = client.post(
        "/v1/code/execute",
        json={
            "language": "python",
            "code": "print('hello')"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "stdout" in data
    assert "exit_code" in data


def test_billing_balance(client):
    """Test billing balance endpoint"""
    response = client.get("/v1/billing/balance")
    assert response.status_code == 200
    data = response.json()
    assert "balance" in data


def test_marketplace_job_submission(client):
    """Test marketplace job submission"""
    response = client.post(
        "/v1/marketplace/jobs",
        json={
            "job_type": "llm_inference",
            "specs": {"model": "llama-3-8b"},
            "max_price": 100,
            "timeout": 60
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
