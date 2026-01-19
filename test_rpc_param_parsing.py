#!/usr/bin/env python3
"""Test RPC param parsing for wrapped params format."""

import json
from typing import Any


def _parse_params(params_args: list[str]) -> Any:
    """Copy of the updated _parse_params from rpc.py"""
    if not params_args:
        return []

    def _parse_value(value: str) -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    if len(params_args) == 1:
        parsed = _parse_value(params_args[0])
        
        # Handle wrapped params format: {"params": [...]} or {"params": {...}}
        # This allows: animica rpc call state.getBalance '{"params":["anim1..."]}'
        if isinstance(parsed, dict) and "params" in parsed:
            inner_params = parsed["params"]
            # Return the unwrapped params (array or dict)
            return inner_params
        
        if isinstance(parsed, (list, dict)):
            return parsed
        return [parsed]

    return [_parse_value(arg) for arg in params_args]


def test_rpc_param_parsing():
    """Test all param parsing scenarios."""
    print("Testing RPC param parsing...")
    
    # Test 1: Wrapped params format (NEW - fixes -32602 error)
    result1 = _parse_params(['{"params":["anim1abc"]}'])
    print(f"  Wrapped params: {result1}")
    assert result1 == ["anim1abc"], f"Expected ['anim1abc'], got {result1}"
    
    # Test 2: Array params format (existing)
    result2 = _parse_params(['["anim1abc"]'])
    print(f"  Array params: {result2}")
    assert result2 == ["anim1abc"], f"Expected ['anim1abc'], got {result2}"
    
    # Test 3: Dict params format (existing)
    result3 = _parse_params(['{"address":"anim1abc"}'])
    print(f"  Dict params: {result3}")
    assert result3 == {"address": "anim1abc"}, f"Expected dict, got {result3}"
    
    # Test 4: String param (existing)
    result4 = _parse_params(['anim1abc'])
    print(f"  String param: {result4}")
    assert result4 == ["anim1abc"], f"Expected ['anim1abc'], got {result4}"
    
    # Test 5: Empty params (existing)
    result5 = _parse_params([])
    print(f"  Empty params: {result5}")
    assert result5 == [], f"Expected [], got {result5}"
    
    # Test 6: Wrapped dict params
    result6 = _parse_params(['{"params":{"address":"anim1abc"}}'])
    print(f"  Wrapped dict params: {result6}")
    assert result6 == {"address": "anim1abc"}, f"Expected dict, got {result6}"
    
    print("✓ All RPC param parsing tests passed!")
    return True


if __name__ == "__main__":
    test_rpc_param_parsing()
