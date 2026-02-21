from __future__ import annotations


class FeeRoutingService:
    def estimate(self, token_count: int) -> dict:
        total = max(1, token_count // 50)
        aicf = int(total * 0.6)
        operator = total - aicf
        return {"fee_total_anm": total, "aicf_split_anm": aicf, "operator_split_anm": operator}

    def validate_credit_increment(self, before: int, after: int, expected_min: int = 1) -> tuple[bool, str]:
        delta = after - before
        if delta >= expected_min:
            return True, f"credits increased by {delta}"
        return False, f"credits did not increase enough (delta={delta})"
