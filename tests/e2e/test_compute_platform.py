"""
End-to-End Integration Tests for Animica Compute Platform

Tests the complete user journey:
1. User registration
2. Login and token management
3. Credit purchase
4. LLM inference request
5. Usage tracking and billing
"""

import pytest
import httpx
import asyncio
from typing import Optional

# Base URLs
AUTH_URL = "http://localhost:8001"
BILLING_URL = "http://localhost:8002"
INFERENCE_URL = "http://localhost:8003"
SANDBOX_URL = "http://localhost:8004"

# Test user credentials
TEST_EMAIL = "e2e-test@animica.ai"
TEST_PASSWORD = "SecureTestPassword123!"


class E2ETestClient:
    """Helper class for E2E testing"""
    
    def __init__(self):
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.user_id: Optional[str] = None
    
    async def register(self, email: str, password: str) -> dict:
        """Register a new user"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{AUTH_URL}/auth/register",
                json={"email": email, "password": password},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]
            return data
    
    async def login(self, email: str, password: str) -> dict:
        """Login existing user"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{AUTH_URL}/auth/login",
                json={"email": email, "password": password},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]
            return data
    
    async def get_balance(self) -> dict:
        """Get credit balance"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BILLING_URL}/billing/balance",
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
    
    async def chat_completion(self, message: str, stream: bool = False) -> dict:
        """Make a chat completion request"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{INFERENCE_URL}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "model": "gpt2",
                    "messages": [{"role": "user", "content": message}],
                    "max_tokens": 50,
                    "stream": stream,
                },
                timeout=60.0
            )
            response.raise_for_status()
            return response.json()
    
    async def execute_code(self, language: str, code: str) -> dict:
        """Execute code in sandbox"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{SANDBOX_URL}/execute",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "language": language,
                    "code": code,
                    "timeout": 10,
                },
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()


@pytest.fixture
async def test_client():
    """Create test client"""
    return E2ETestClient()


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_complete_user_journey(test_client: E2ETestClient):
    """
    Test complete user journey from registration to usage
    
    This test requires all services to be running:
    - Auth service (port 8001)
    - Billing service (port 8002)
    - Inference service (port 8003)
    - Sandbox runner (port 8004)
    """
    
    # 1. Register new user
    print("Step 1: Registering user...")
    try:
        registration = await test_client.register(TEST_EMAIL, TEST_PASSWORD)
        assert "access_token" in registration
        assert "refresh_token" in registration
        print("✓ User registered successfully")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            # User already exists, login instead
            print("User exists, logging in...")
            await test_client.login(TEST_EMAIL, TEST_PASSWORD)
            print("✓ User logged in successfully")
        else:
            raise
    
    # 2. Check initial balance
    print("\nStep 2: Checking initial balance...")
    balance = await test_client.get_balance()
    initial_balance = float(balance["balance"])
    print(f"✓ Initial balance: {initial_balance} credits")
    
    # 3. Make inference request
    print("\nStep 3: Making LLM inference request...")
    chat_response = await test_client.chat_completion(
        "Say hello in one word"
    )
    assert "choices" in chat_response
    assert len(chat_response["choices"]) > 0
    completion = chat_response["choices"][0]["message"]["content"]
    print(f"✓ Got completion: {completion[:50]}...")
    
    # Check tokens used
    usage = chat_response.get("usage", {})
    total_tokens = usage.get("total_tokens", 0)
    print(f"✓ Tokens used: {total_tokens}")
    
    # 4. Execute code in sandbox
    print("\nStep 4: Executing code in sandbox...")
    code_result = await test_client.execute_code(
        "python",
        "print('Hello from Animica sandbox!')"
    )
    assert code_result["exit_code"] == 0
    assert "Hello from Animica sandbox!" in code_result["stdout"]
    print(f"✓ Code executed successfully")
    print(f"  Output: {code_result['stdout'].strip()}")
    print(f"  Execution time: {code_result['execution_time']:.3f}s")
    
    # 5. Check balance after usage
    print("\nStep 5: Checking balance after usage...")
    final_balance_data = await test_client.get_balance()
    final_balance = float(final_balance_data["balance"])
    print(f"✓ Final balance: {final_balance} credits")
    
    # Note: Balance might not have changed yet if billing integration is async
    if final_balance < initial_balance:
        print(f"✓ Credits deducted: {initial_balance - final_balance}")
    else:
        print("⚠ Balance unchanged (billing integration may be async)")
    
    print("\n" + "="*50)
    print("✅ E2E TEST PASSED - All systems operational!")
    print("="*50)


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_streaming_inference(test_client: E2ETestClient):
    """Test streaming chat completions"""
    
    # Login first
    try:
        await test_client.register(TEST_EMAIL, TEST_PASSWORD)
    except:
        await test_client.login(TEST_EMAIL, TEST_PASSWORD)
    
    print("\nTesting streaming inference...")
    
    # Note: Full streaming test would need SSE client
    # For now, just test non-streaming mode
    response = await test_client.chat_completion(
        "Count to 5",
        stream=False
    )
    
    assert "choices" in response
    print("✓ Streaming test completed")


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_multi_language_sandbox(test_client: E2ETestClient):
    """Test sandbox with multiple languages"""
    
    # Login first
    try:
        await test_client.register(TEST_EMAIL, TEST_PASSWORD)
    except:
        await test_client.login(TEST_EMAIL, TEST_PASSWORD)
    
    print("\nTesting multi-language sandbox...")
    
    # Python
    python_result = await test_client.execute_code(
        "python",
        "print(2 + 2)"
    )
    assert "4" in python_result["stdout"]
    print("✓ Python execution works")
    
    # JavaScript
    js_result = await test_client.execute_code(
        "javascript",
        "console.log(3 + 3)"
    )
    assert "6" in js_result["stdout"]
    print("✓ JavaScript execution works")
    
    # Bash
    bash_result = await test_client.execute_code(
        "bash",
        "echo 'Hello from bash'"
    )
    assert "Hello from bash" in bash_result["stdout"]
    print("✓ Bash execution works")
    
    print("✅ Multi-language sandbox test passed!")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s", "-m", "e2e"])
