"""Test seed parsing fix for nodes not connecting."""

import sys
from pathlib import Path

# Add p2p module to path
sys.path.insert(0, str(Path(__file__).parent / "p2p"))

from p2p.core_p2p.service import _parse_seed


def test_seed_parsing_with_port():
    """Test seed parsing with explicit port."""
    result = _parse_seed("127.0.0.1:30333")
    assert result is not None, "Failed to parse seed with port"
    assert result.port == 30333, f"Expected port 30333, got {result.port}"
    print("✓ Seed with port: 127.0.0.1:30333 -> port 30333")


def test_seed_parsing_without_port():
    """Test seed parsing without port (should use default 30333)."""
    result = _parse_seed("127.0.0.1")
    assert result is not None, "Failed to parse seed without port"
    assert result.port == 30333, f"Expected default port 30333, got {result.port}"
    print("✓ Seed without port: 127.0.0.1 -> port 30333 (default)")


def test_seed_parsing_hostname_with_port():
    """Test seed parsing with hostname and port."""
    result = _parse_seed("mainnet.animica.org:30333")
    assert result is not None, "Failed to parse hostname with port"
    assert result.port == 30333, f"Expected port 30333, got {result.port}"
    print("✓ Hostname with port: mainnet.animica.org:30333 -> port 30333")


def test_seed_parsing_hostname_without_port():
    """Test seed parsing with hostname without port (should use default)."""
    # Note: This will fail if DNS resolution fails, but the parsing logic
    # should work correctly
    result = _parse_seed("localhost")
    assert result is not None, "Failed to parse hostname without port"
    assert result.port == 30333, f"Expected default port 30333, got {result.port}"
    print("✓ Hostname without port: localhost -> port 30333 (default)")


def test_seed_parsing_empty():
    """Test seed parsing with empty string."""
    result = _parse_seed("")
    assert result is None, "Expected None for empty seed"
    print("✓ Empty seed: '' -> None")


def test_seed_parsing_invalid():
    """Test seed parsing with invalid format."""
    result = _parse_seed("invalid:port:format")
    # This should parse as "invalid:port" with "format" as port which will fail
    assert result is None, "Expected None for invalid seed format"
    print("✓ Invalid seed: 'invalid:port:format' -> None")


if __name__ == "__main__":
    print("Testing seed parsing fix...\n")
    
    try:
        test_seed_parsing_with_port()
        test_seed_parsing_without_port()
        test_seed_parsing_hostname_with_port()
        test_seed_parsing_hostname_without_port()
        test_seed_parsing_empty()
        test_seed_parsing_invalid()
        
        print("\n✅ All tests passed! Seed parsing fix is working correctly.")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
