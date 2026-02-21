"""Transaction simulation script template."""


def run_call(payload: dict) -> dict:
    return {"ok": True, "payload": payload}


def simulate_tx(payload: dict) -> dict:
    preview = run_call(payload)
    return {"status": "simulated", "preview": preview}


if __name__ == "__main__":
    result = simulate_tx({"to": "0xabc", "amount": 1})
    print(result)
