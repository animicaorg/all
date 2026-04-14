from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from core.types.header import Header, serialize_header
from core.utils.hash import sha3_256

from .templates import HeaderTemplate

ZERO32 = b"\x00" * 32


def int_from_value(value: Any, *, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        if isinstance(value, str) and value.startswith("0x"):
            return int(value, 16)
        return int(value)
    except Exception:
        return default


def parse_hex_bytes(value: Any, *, default: bytes = ZERO32) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return bytes.fromhex(value[2:])
        except Exception:
            return default
    return default


def encode_header_payload(header: Header) -> dict[str, Any]:
    return {
        key: ("0x" + value.hex() if isinstance(value, (bytes, bytearray)) else value)
        for key, value in header.to_obj().items()
    }


def header_from_template_view(header_view: Mapping[str, Any], *, nonce: int | None = None) -> Header:
    resolved_nonce = (
        int_from_value(header_view.get("nonce"), default=0)
        if nonce is None
        else int(nonce)
    )
    return Header(
        v=int_from_value(header_view.get("v"), default=1),
        chainId=int_from_value(
            header_view.get("chainId") or header_view.get("chain_id")
        ),
        height=int_from_value(
            header_view.get("height") or header_view.get("number")
        ),
        parentHash=parse_hex_bytes(
            header_view.get("parentHash") or header_view.get("parent_hash")
        ),
        timestamp=int_from_value(header_view.get("timestamp")),
        stateRoot=parse_hex_bytes(
            header_view.get("stateRoot") or header_view.get("state_root")
        ),
        txsRoot=parse_hex_bytes(
            header_view.get("txsRoot") or header_view.get("txs_root")
        ),
        receiptsRoot=parse_hex_bytes(
            header_view.get("receiptsRoot") or header_view.get("receipts_root")
        ),
        proofsRoot=parse_hex_bytes(
            header_view.get("proofsRoot") or header_view.get("proofs_root")
        ),
        daRoot=parse_hex_bytes(
            header_view.get("daRoot") or header_view.get("da_root")
        ),
        mixSeed=parse_hex_bytes(
            header_view.get("mixSeed") or header_view.get("mix_seed")
        ),
        poiesPolicyRoot=parse_hex_bytes(
            header_view.get("poiesPolicyRoot") or header_view.get("poies_policy_root")
        ),
        pqAlgPolicyRoot=parse_hex_bytes(
            header_view.get("pqAlgPolicyRoot") or header_view.get("pq_alg_policy_root")
        ),
        thetaMicro=int_from_value(
            header_view.get("thetaMicro")
            or header_view.get("thetaTargetMicro")
            or header_view.get("theta_target_micro")
            or header_view.get("theta_micro")
        ),
        workType=int_from_value(
            header_view.get("workType")
            if header_view.get("workType") is not None
            else header_view.get("work_type"),
            default=0,
        ),
        nonce=resolved_nonce,
        extra=parse_hex_bytes(header_view.get("extra"), default=b""),
    )


def header_sign_bytes_from_template_view(header_view: Mapping[str, Any]) -> bytes:
    header_tpl = HeaderTemplate(
        parent_hash=parse_hex_bytes(header_view.get("parentHash")),
        number=int_from_value(header_view.get("height") or header_view.get("number")),
        chain_id=int_from_value(
            header_view.get("chainId") or header_view.get("chain_id")
        ),
        state_root=parse_hex_bytes(header_view.get("stateRoot")),
        txs_root=parse_hex_bytes(header_view.get("txsRoot")),
        receipts_root=parse_hex_bytes(header_view.get("receiptsRoot")),
        proofs_root=parse_hex_bytes(header_view.get("proofsRoot")),
        da_root=parse_hex_bytes(header_view.get("daRoot")),
        theta_target_micro=int_from_value(
            header_view.get("thetaMicro")
            or header_view.get("thetaTargetMicro")
            or header_view.get("theta_target_micro")
            or header_view.get("theta_micro")
        ),
        mix_seed=parse_hex_bytes(header_view.get("mixSeed")),
        pq_alg_policy_root=parse_hex_bytes(header_view.get("pqAlgPolicyRoot")),
        poies_policy_root=parse_hex_bytes(header_view.get("poiesPolicyRoot")),
        timestamp=int_from_value(header_view.get("timestamp")),
        work_type=int_from_value(header_view.get("workType"), default=0),
    )
    return header_tpl.to_sign_bytes()


def looks_like_block_template(work: Mapping[str, Any]) -> bool:
    header = work.get("header")
    return (
        isinstance(header, Mapping)
        and bool(work.get("target"))
        and (
            work.get("templateId")
            or work.get("template_id")
            or work.get("parentHash")
            or work.get("parent")
            or isinstance(work.get("txs"), list)
        )
    )


@dataclass(frozen=True)
class CandidateHeaderHash:
    header: Header
    digest: bytes
    digest_int: int


def hash_candidate_header(
    header_or_view: Header | Mapping[str, Any],
    *,
    nonce: int | None = None,
) -> CandidateHeaderHash:
    if isinstance(header_or_view, Header):
        header = header_or_view if nonce is None else replace(header_or_view, nonce=int(nonce))
    else:
        header = header_from_template_view(header_or_view, nonce=nonce)
    digest = sha3_256(serialize_header(header))
    return CandidateHeaderHash(
        header=header,
        digest=digest,
        digest_int=int.from_bytes(digest, "big", signed=False),
    )


def build_submit_block_payload(
    template: Mapping[str, Any],
    *,
    nonce: int,
) -> dict[str, Any]:
    header_view = template.get("header") or {}
    if not isinstance(header_view, Mapping):
        raise ValueError("template header is required")

    header = header_from_template_view(header_view, nonce=nonce)
    txs_raw: list[Any] = []
    for entry in template.get("txs") or []:
        if isinstance(entry, Mapping):
            raw = entry.get("raw")
            if raw is not None:
                txs_raw.append(raw)
        else:
            txs_raw.append(entry)

    proofs = template.get("proofs")
    if not isinstance(proofs, list):
        proofs = []

    payload: dict[str, Any] = {
        "header": encode_header_payload(header),
        "txs": txs_raw,
        "proofs": proofs,
    }
    template_id = template.get("templateId") or template.get("template_id")
    if template_id:
        payload["templateId"] = template_id

    parent = template.get("parent") if isinstance(template.get("parent"), Mapping) else {}
    parent_hash = (
        template.get("parentHash")
        or parent.get("hash")
        or payload["header"].get("parentHash")
    )
    if parent_hash:
        payload["parentHash"] = parent_hash
    return payload


def template_tx_count(template: Mapping[str, Any]) -> int:
    txs = template.get("txs")
    return len(txs) if isinstance(txs, list) else 0
