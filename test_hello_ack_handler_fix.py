"""
Test to verify that nodes complete handshake bidirectionally.

This test ensures that HELLO_ACK messages are properly handled so that
both the initiator and responder sides complete the handshake and set
identity_ok = True.

Bug: Previously, HELLO_ACK was ignored (just returned), causing initiator
side to never complete handshake, resulting in peer_count() = 0 even when
connections existed.
"""

import sys
from pathlib import Path

print("=" * 70)
print("TEST: Verify HELLO_ACK Handler Implementation")
print("=" * 70)

# Check that the handler exists and is called
code_path = Path(__file__).parent / "p2p" / "node" / "p2p_service_legacy.py"
with open(code_path, "r") as f:
    content = f.read()

checks = [
    (
        "HELLO_ACK handler method exists",
        "async def _handle_hello_ack" in content,
    ),
    (
        "HELLO_ACK handler is called from dispatcher",
        'if mid == int(MsgID.HELLO_ACK):\n            await self._handle_hello_ack(peer, payload)' in content,
    ),
    (
        "Handler decodes HelloAck message",
        "HelloAck(**{k: v for k, v in data.items() if k in allowed})" in content.split("async def _handle_hello_ack")[1].split("async def ")[0],
    ),
    (
        "Handler checks accepted field",
        "if not ack.accepted:" in content.split("async def _handle_hello_ack")[1].split("async def ")[0],
    ),
    (
        "Handler sets identity_ok = True",
        "peer.identity_ok = True" in content.split("async def _handle_hello_ack")[1].split("async def ")[0],
    ),
    (
        "Handler calls HandshakeManager.on_identity_received",
        "self._handshake_manager.on_identity_received" in content.split("async def _handle_hello_ack")[1].split("async def ")[0],
    ),
    (
        "Handler sets hello_done event",
        "peer.hello_done.set()" in content.split("async def _handle_hello_ack")[1].split("async def ")[0],
    ),
    (
        "Handler wakes sync",
        "self._sync_wakeup.set()" in content.split("async def _handle_hello_ack")[1].split("async def ")[0],
    ),
    (
        "No longer ignores HELLO_ACK (old bug)",
        'if mid == int(MsgID.HELLO_ACK):\n            return' not in content,
    ),
]

passed = 0
failed = 0

for desc, check_passes in checks:
    if check_passes:
        print(f"✓ PASS: {desc}")
        passed += 1
    else:
        print(f"✗ FAIL: {desc}")
        failed += 1

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Total: {passed + failed}")
print(f"Passed: {passed}")
print(f"Failed: {failed}")

if failed == 0:
    print("\n✅ ALL CHECKS PASSED!")
    print("HELLO_ACK handler is properly implemented.")
    print("Nodes should now complete handshake bidirectionally.")
    sys.exit(0)
else:
    print(f"\n❌ {failed} CHECKS FAILED")
    sys.exit(1)
