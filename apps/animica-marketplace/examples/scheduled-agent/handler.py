# scheduled-agent — a chain monitor driven by a CloudSchedule, with persistent memory.
#
# Each run reads the live Animica chain head (READ_CHAIN) and compares it against what the
# PREVIOUS run recorded in animica.state (PERSIST_STATE) — the per-function encrypted
# key/value store that survives between executions. The CloudSchedule created by
# scripts/cloud-examples.ts fires this hourly; the scheduler invokes it exactly like any
# other execution (callerKind "schedule", the owner pays), so nothing here is
# schedule-specific — the same function can also be called on demand.

import time

import animica

MAX_HISTORY = 48  # bounded memory: state values are capped at 16 KB each


def main(request, ctx):
    head = animica.chain.head()
    height = int(head.get("height") or head.get("number") or 0)
    now = int(time.time())

    prev = animica.state.get("last_check") or {}
    history = animica.state.get("history") or []

    new_blocks = None
    elapsed_s = None
    if isinstance(prev, dict) and prev.get("height"):
        new_blocks = height - int(prev["height"])
        elapsed_s = now - int(prev.get("t", now))

    history.append({"t": now, "height": height, "new_blocks": new_blocks})
    history = history[-MAX_HISTORY:]

    animica.state.set("last_check", {"height": height, "t": now})
    animica.state.set("history", history)

    if new_blocks is None:
        animica.log(f"first run: chain head is {height}")
    else:
        animica.log(f"chain head {height}: {new_blocks} new block(s) in {elapsed_s}s")

    return {
        "height": height,
        "head_hash": head.get("hash"),
        "new_blocks_since_last_run": new_blocks,
        "seconds_since_last_run": elapsed_s,
        "runs_recorded": len(history),
        "caller": ctx.caller,
        "request_id": ctx.request_id,
    }
