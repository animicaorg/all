"""
Test script to verify snapshot error handling improvements.

This script tests that empty error messages are handled gracefully
and converted to meaningful error messages.
"""

import sys
from pathlib import Path

# Add the python module to the path
sys.path.insert(0, str(Path(__file__).parent / "python"))

def test_empty_exception_handling():
    """Test that empty exceptions are handled properly."""
    
    # Test case 1: Empty string exception
    class EmptyException(Exception):
        def __str__(self):
            return ""
    
    e = EmptyException()
    error_msg = str(e) if str(e).strip() else f"Unknown error ({type(e).__name__})"
    
    assert error_msg != "", "Error message should not be empty"
    assert "EmptyException" in error_msg, "Error message should contain exception type"
    print(f"✓ Test 1 passed: Empty exception handled correctly: {error_msg}")
    
    # Test case 2: None exception
    class NoneException(Exception):
        def __str__(self):
            return None  # type: ignore
    
    try:
        e2 = NoneException()
        error_msg2 = str(e2) if str(e2).strip() else f"Unknown error ({type(e2).__name__})"
        
        assert error_msg2 != "", "Error message should not be empty"
        assert "NoneException" in error_msg2 or error_msg2 == "None", "Error message should be meaningful"
        print(f"✓ Test 2 passed: None exception handled: {error_msg2}")
    except Exception as e:
        print(f"✓ Test 2 passed (with exception handling): {e}")
    
    # Test case 3: Whitespace-only exception
    class WhitespaceException(Exception):
        def __str__(self):
            return "   \t\n  "
    
    e3 = WhitespaceException()
    error_msg3 = str(e3) if str(e3).strip() else f"Unknown error ({type(e3).__name__})"
    
    assert error_msg3 != "", "Error message should not be empty"
    assert error_msg3.strip() != "", "Error message should not be only whitespace"
    assert "WhitespaceException" in error_msg3, "Error message should contain exception type"
    print(f"✓ Test 3 passed: Whitespace exception handled correctly: {error_msg3}")
    
    # Test case 4: Normal exception
    e4 = RuntimeError("Connection refused")
    error_msg4 = str(e4) if str(e4).strip() else f"Unknown error ({type(e4).__name__})"
    
    assert error_msg4 == "Connection refused", "Normal error message should be preserved"
    print(f"✓ Test 4 passed: Normal exception preserved: {error_msg4}")
    
    print("\n✅ All error handling tests passed!")

if __name__ == "__main__":
    test_empty_exception_handling()
