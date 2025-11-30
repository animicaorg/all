STATE = {"providers": {}, "balances": {}, "heartbeats": {}, "jobs": []}


def add_balance(pid: str, amt: int) -> int:
    STATE["balances"][pid] = int(STATE["balances"].get(pid, 0)) + int(amt)
    return STATE["balances"][pid]
