from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional


VerifyFn = Callable[[bytes, bytes, bytes], bool]
SignFn = Callable[[bytes, bytes], bytes]


@dataclass(frozen=True)
class SchemeSpec:
    scheme_id: int
    name: str
    pubkey_lengths: tuple[int, ...]
    signature_lengths: tuple[int, ...]
    enabled_by_default: bool = True


@dataclass
class RuntimeScheme:
    spec: SchemeSpec
    sign_fn: Optional[SignFn] = None
    verify_fn: Optional[VerifyFn] = None
    enabled: bool = False
    reason_if_disabled: Optional[str] = None


CANONICAL_SCHEME_SPECS: tuple[SchemeSpec, ...] = (
    SchemeSpec(
        scheme_id=1,
        name="dilithium3",
        pubkey_lengths=(1952,),
        signature_lengths=(3293,),
        enabled_by_default=True,
    ),
    SchemeSpec(
        scheme_id=2,
        name="sphincs_shake_128s",
        pubkey_lengths=(32,),
        signature_lengths=(7856,),
        enabled_by_default=True,
    ),
    SchemeSpec(
        scheme_id=3,
        name="sphincs_shake_128f",
        pubkey_lengths=(32,),
        signature_lengths=(17088,),
        enabled_by_default=True,
    ),
    SchemeSpec(
        scheme_id=4,
        name="sphincs_shake_256s",
        pubkey_lengths=(64,),
        signature_lengths=(29792,),
        enabled_by_default=True,
    ),
)


def load_policy_disabled_scheme_ids() -> set[int]:
    raw = (os.environ.get("ANIMICA_DISABLED_SIGNATURE_SCHEMES") or "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part, 0))
        except ValueError:
            continue
    return out


def build_runtime_scheme_table() -> dict[int, RuntimeScheme]:
    return {spec.scheme_id: RuntimeScheme(spec=spec) for spec in CANONICAL_SCHEME_SPECS}
