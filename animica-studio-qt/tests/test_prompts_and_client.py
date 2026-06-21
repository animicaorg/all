"""Tests for prompt helpers and the client's native tool-call parsing."""
from __future__ import annotations

from animica_studio.agent import prompts
from animica_studio.services.animica_client import (
    ChatResult,
    _accumulate_tool_call_deltas,
    _extract_tool_calls,
)


def test_strip_tool_call_fences_removes_protocol_blocks():
    text = (
        "Sure, I'll do that.\n\n"
        "```tool_call\n{\"tool\": \"read_file\", \"arguments\": {\"path\": \"a.py\"}}\n```\n\n"
        "Reading now."
    )
    out = prompts.strip_tool_call_fences(text)
    assert "tool_call" not in out
    assert "read_file" not in out
    assert "Sure, I'll do that." in out
    assert "Reading now." in out


def test_strip_tool_call_fences_keeps_normal_code():
    text = "Here:\n```python\nprint('hi')\n```\n"
    out = prompts.strip_tool_call_fences(text)
    assert "print('hi')" in out


def test_strip_tool_call_fences_unterminated():
    text = "Working on it ```tool_call\n{\"tool\": \"x\""
    out = prompts.strip_tool_call_fences(text)
    assert "tool_call" not in out
    assert out.startswith("Working on it")


def test_build_plan_message_renders_steps():
    from animica_studio.models import PlanStep

    steps = [PlanStep(index=0, title="Read", detail="read main.py"), PlanStep(index=1, title="Edit")]
    msg = prompts.build_plan_message("Do the thing", steps, ["main.py"])
    assert "Do the thing" in msg
    assert "1. Read — read main.py" in msg
    assert "2. Edit" in msg
    assert "main.py" in msg


def test_extract_tool_calls_native():
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}},
        ],
    }
    calls = _extract_tool_calls(message)
    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert calls[0]["name"] == "read_file"
    assert "a.py" in calls[0]["arguments"]


def test_extract_tool_calls_legacy_function_call():
    message = {"function_call": {"name": "do_it", "arguments": "{}"}}
    calls = _extract_tool_calls(message)
    assert calls and calls[0]["name"] == "do_it"


def test_accumulate_streamed_tool_call_deltas():
    buf: dict = {}
    # Streamed in fragments, like a real SSE stream.
    _accumulate_tool_call_deltas(buf, [{"index": 0, "id": "c1", "function": {"name": "write_file", "arguments": "{\"path\":"}}])
    _accumulate_tool_call_deltas(buf, [{"index": 0, "function": {"arguments": " \"a.py\", \"content\": \"x\"}"}}])
    assert buf[0]["name"] == "write_file"
    assert buf[0]["arguments"] == "{\"path\": \"a.py\", \"content\": \"x\"}"
    assert buf[0]["id"] == "c1"


def test_chat_result_str_is_text():
    r = ChatResult(text="hello", tool_calls=[])
    assert str(r) == "hello"
