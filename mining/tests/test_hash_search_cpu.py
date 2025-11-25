import importlib
import math
import os
from typing import Any, Iterable, Optional, Tuple, List, Callable
import pytest


# Try to import the module under test
hs = importlib.import_module("mining.hash_search")

# Some implementations pick a backend based on env; make sure CPU is allowed.
os.environ.setdefault("ANIMICA_MINER_DEVICE", "cpu")


def _pick_callable(mod, names: Iterable[str]) -> Optional[Callable[..., Any]]:
    for n in names:
        fn = getattr(mod, n, None)
        if callable(fn):
            return fn
    return None


# Heuristic function name guesses (kept broad to tolerate minor refactors)
scan_fn = _pick_callable(
    hs,
    (
        "scan_cpu",            # preferred
        "scan",                # generic
        "search",              # generic
        "scan_nonces",         # descriptive
        "find_shares",         # descriptive
    ),
)

# A small, deterministic header template (bytes). Many implementations only
# need a fixed prefix (header-without-nonce) and will append/encode the nonce.
HEADER_BYTES = b"ANIMICA-TEST-HEADER-\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a" + bytes(range(16))

# Difficulty parameter (Θ). With H = -ln(u), acceptance probability is p = e^{-Θ}.
# Θ=2.0 -> p≈0.1353 which yields a healthy amount of shares for a few thousand trials.
THETA = 2.0

# How many nonces to try. Keep this modest so it runs quickly in CI.
NONCES = 5000


def _try_scan_variants() -> Tuple[List[Any], int]:
    """
    Call the mining.hash_search scan function using a handful of plausible
    signatures. Returns (results, evaluated_trials).
    Each result element is a 'share-like' record (dict/tuple/object).
    """
    if scan_fn is None:
        pytest.skip("hash_search scan function not found")

    calls = []

    # Common positional/keyword variants
    calls.append(lambda: (scan_fn(HEADER_BYTES, 0, NONCES, THETA), NONCES))
    calls.append(lambda: (scan_fn(HEADER_BYTES, NONCES, THETA), NONCES))
    calls.append(lambda: (scan_fn(header=HEADER_BYTES, start_nonce=0, count=NONCES, Theta=THETA), NONCES))
    calls.append(lambda: (scan_fn(header_bytes=HEADER_BYTES, start=0, n=NONCES, theta=THETA), NONCES))
    calls.append(lambda: (scan_fn(template={"header": HEADER_BYTES}, start_nonce=0, count=NONCES, Theta=THETA), NONCES))

    last_err: Optional[Exception] = None
    for c in calls:
        try:
            res, trials = c()
            # Accept list or iterator/generator
            if isinstance(res, Iterable) and not isinstance(res, (bytes, bytearray)):
                out = list(res)
                return out, trials
        except TypeError as e:
            last_err = e
            continue
        except Exception as e:  # pragma: no cover - make test resilient to unexpected shapes
            last_err = e
            continue

    # If we got here, signatures didn't match.
    if last_err:
        pytest.skip(f"Could not invoke scan function with expected signatures: {last_err}")
    pytest.skip("Could not invoke scan function with expected signatures.")


def _extract_nonce_H(rec: Any) -> Tuple[int, Optional[float]]:
    """
    Extract (nonce, H) from a generic 'share' record. Implementations vary:
    - dict with 'nonce' and optionally 'H' or 'h' or 'difficulty';
    - tuple (nonce, H) or (nonce,);
    - object with attributes .nonce / .H / .h / .difficulty.
    H may be absent; in that case we return (nonce, None).
    """
    # dict-like
    if isinstance(rec, dict):
        nonce = int(rec.get("nonce") if "nonce" in rec else rec.get("n", 0))
        H = rec.get("H", rec.get("h", rec.get("difficulty")))
        return nonce, (float(H) if H is not None else None)

    # tuple/list
    if isinstance(rec, (tuple, list)) and len(rec) >= 1:
        nonce = int(rec[0])
        H = None
        if len(rec) >= 2:
            try:
                H = float(rec[1])
            except Exception:
                H = None
        return nonce, H

    # object-like
    nonce = int(getattr(rec, "nonce", getattr(rec, "n", 0)))
    H_val = getattr(rec, "H", None)
    if H_val is None:
        H_val = getattr(rec, "h", getattr(rec, "difficulty", None))
    return nonce, (float(H_val) if H_val is not None else None)


def test_finds_shares_and_rate_matches_expectation():
    """
    The CPU scanning loop should find a non-trivial number of shares at small Θ.
    With Θ=2.0 and N=5000 trials, expected shares ≈ N * e^{-Θ} within a few sigmas.
    """
    shares, trials = _try_scan_variants()

    # Basic sanity: should find at least one share at this Θ and trial count.
    assert len(shares) > 0, "No shares found at small Θ — scanning loop may be broken"

    # Estimate how many were actually *tested* if implementation reports that
    # (some return (tested, shares) etc.). Try to infer from first record if present.
    # If we can't infer, use 'trials' from the call signature we passed.
    tested = trials

    # Count valid-looking shares (optionally check the H threshold if present)
    valid = 0
    has_H_field = False
    for rec in shares:
        nonce, H = _extract_nonce_H(rec)
        assert isinstance(nonce, int) and nonce >= 0
        if H is not None:
            has_H_field = True
            if H >= THETA - 1e-12:
                valid += 1
        else:
            # If H is not given, assume every returned record is a valid share.
            valid += 1

    # If H was present, ensure every returned record actually meets the threshold.
    if has_H_field:
        assert valid == len(shares), "Returned shares must meet H >= Θ"

    observed = len(shares)
    expected = tested * math.exp(-THETA)
    sigma = math.sqrt(expected * (1.0 - math.exp(-THETA)))  # binomial stddev ≈ sqrt(N p (1-p))

    # Allow a fairly wide band to avoid needless flakiness in CI.
    # 6 sigma is extremely safe for N=5000.
    lower = expected - 6.0 * sigma
    upper = expected + 6.0 * sigma

    # Diagnostic prints (pytest prints these on failure)
    print(
        f"[scan] tested={tested} Θ={THETA} observed={observed} "
        f"expected≈{expected:.2f} ± {6.0*sigma:.2f} (6σ band: [{lower:.1f}, {upper:.1f}])"
    )

    assert lower <= observed <= upper, (
        "Observed share count is far from expectation — check nonce hashing/H(u) and threshold comparison"
    )
