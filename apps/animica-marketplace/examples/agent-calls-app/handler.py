# agent-calls-app — nested execution: one function calls another with animica.call().
#
# animica.call("owner/slug", payload) invokes another deployed function as a NESTED execution
# inside the same call tree. The host broker enforces, server-side, on every hop:
#   * the CALL_FUNCTION capability (declared by THIS function at deploy time),
#   * the maximum call depth (default 4) and per-execution call budget (default 16),
#   * the shared spend budget of the whole tree — a nested paid call (the default target here
#     is examples/anm-toolkit, which carries a per-call surcharge) draws down the SAME
#     authorization the root caller granted, so a chain of agents can never spend more than
#     the caller approved,
#   * no self-calls (a function may not call itself).
#
# The nested result arrives with its own request id and exact cost, which this function
# passes through — so the caller sees precisely what the delegation cost.

import animica

DEFAULT_TARGET = "examples/anm-toolkit"


def main(request, ctx):
    req = request if isinstance(request, dict) else {}
    target = str(req.get("target") or DEFAULT_TARGET)
    payload = req.get("payload")
    if not isinstance(payload, dict):
        payload = {"op": "split", "amount_nanm": "250000000", "fee_bps": 2000}

    animica.log(f"delegating to {target}")
    nested = animica.call(target, payload)

    return {
        "target": target,
        "nested_status": nested.get("status"),
        "nested_request_id": nested.get("request_id"),
        "nested_cost_nanm": nested.get("cost_nanm"),
        "nested_error": nested.get("error"),
        "result": nested.get("result"),
        "request_id": ctx.request_id,
    }
