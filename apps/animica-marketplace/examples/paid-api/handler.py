# paid-api ("anm-toolkit") — a function with a per-call surcharge.
#
# The developer set perCallNanm on this function, so every successful execution adds that
# surcharge on top of the metered resource price. The customer's total payment is then split:
# the platform fee (feeBps, 20% standard) goes to the treasury and the developer receives the
# remainder as an immediately spendable ledger credit. Failed executions charge metered cost
# only — the surcharge is never applied to a run that did not succeed.
#
# The service itself is exact-integer ANM money math (the same discipline the platform uses:
# never floats), so the paid call does something genuinely useful.

NANM_PER_ANM = 1_000_000_000
BECH32_CHARSET = set("qpzry9x8gf2tvdw0s3jn54khce6mua7l")


def _anm_to_nanm(anm: str) -> int:
    s = str(anm).strip()
    if not s or any(c not in "0123456789." for c in s) or s.count(".") > 1:
        raise ValueError(f"invalid ANM amount: {anm!r}")
    whole, _, frac = s.partition(".")
    if len(frac) > 9:
        raise ValueError("ANM has at most 9 decimal places (1 ANM = 1e9 nANM)")
    return int(whole or "0") * NANM_PER_ANM + int((frac + "000000000")[:9] or "0")


def _nanm_to_anm(nanm: int) -> str:
    neg = nanm < 0
    a = -nanm if neg else nanm
    whole, frac = divmod(a, NANM_PER_ANM)
    frac_s = str(frac).zfill(9).rstrip("0")
    return ("-" if neg else "") + (f"{whole}.{frac_s}" if frac_s else str(whole))


def _split(amount_nanm: int, fee_bps: int, provider_bps: int) -> dict:
    """Exact split, floor-per-part, developer takes the remainder — parts always sum to the total."""
    if amount_nanm < 0 or not (0 <= fee_bps <= 10_000) or not (0 <= provider_bps <= 10_000):
        raise ValueError("amount must be >= 0 and bps in 0..10000")
    if fee_bps + provider_bps > 10_000:
        raise ValueError("fee_bps + provider_bps exceeds 100%")
    platform = amount_nanm * fee_bps // 10_000
    provider = amount_nanm * provider_bps // 10_000
    developer = amount_nanm - platform - provider
    return {
        "total_nanm": str(amount_nanm),
        "platform_fee_nanm": str(platform),
        "developer_nanm": str(developer),
        "provider_nanm": str(provider),
        "check_exact_sum": platform + provider + developer == amount_nanm,
    }


def main(request):
    req = request if isinstance(request, dict) else {}
    op = str(req.get("op", "convert"))

    if op == "convert":
        if "anm" in req:
            nanm = _anm_to_nanm(req["anm"])
            return {"op": op, "anm": _nanm_to_anm(nanm), "nanm": str(nanm)}
        nanm = int(str(req.get("nanm", "0")))
        return {"op": op, "anm": _nanm_to_anm(nanm), "nanm": str(nanm)}

    if op == "split":
        return {
            "op": op,
            **_split(
                int(str(req.get("amount_nanm", "0"))),
                int(req.get("fee_bps", 2000)),
                int(req.get("provider_bps", 0)),
            ),
        }

    if op == "validate_address":
        addr = str(req.get("address", ""))
        data = addr[5:] if addr.startswith("anim1") else None
        ok = bool(data) and len(data) >= 20 and all(c in BECH32_CHARSET for c in data)
        return {
            "op": op,
            "address": addr[:90],
            "structurally_valid": ok,
            "note": "prefix + charset + length shape check (not a full bech32m checksum verification)",
        }

    raise ValueError(f"unknown op {op!r}: use convert | split | validate_address")
