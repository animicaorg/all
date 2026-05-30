"""Tests for the Animica Founders Pass launchpad."""

from __future__ import annotations

import pytest

from contracts.tests.nft_harness import (
    addr,
    assert_reverts,
    deploy,
    events_named,
    reset_registry,
)


COLLECTION_PATH = "contracts/standards/animica_nft721/contract.py"
FP_PATH = "contracts/launchpads/animica_founders_pass/contract.py"

NANOS_PER_ANM = 1_000_000_000
PRICE = 25_000 * NANOS_PER_ANM   # 25,000 ANM


@pytest.fixture
def env():
    reset_registry()
    owner = addr("owner")
    alice = addr("alice")
    bob = addr("bob")
    treasury = addr("treasury")
    coll_addr = addr("fp-collection")
    fp_addr = addr("fp-launch")

    # Underlying collection. Capacity is the *Founders Pass* supply.
    coll = deploy(COLLECTION_PATH, coll_addr)
    coll.call(
        "init",
        b"Animica Founders Pass",
        b"AFP",
        owner,
        b"https://meta.animica.xyz/founders/",
        1000,        # max supply matches FP supply cap
        owner,
        500,         # 5% royalty
        caller=owner,
    )

    # Founders Pass launchpad — delegate-mint via set_minter().
    fp = deploy(FP_PATH, fp_addr)
    fp.call("init", owner, coll_addr, treasury, PRICE, 1000, caller=owner)
    coll.call("set_minter", fp_addr, caller=owner)

    return {
        "coll": coll,
        "fp": fp,
        "coll_addr": coll_addr,
        "fp_addr": fp_addr,
        "owner": owner,
        "alice": alice,
        "bob": bob,
        "treasury": treasury,
    }


# ── init ────────────────────────────────────────────────────────────────────


def test_init_sets_defaults(env):
    fp = env["fp"]
    assert fp.call("owner") == env["owner"]
    assert fp.call("collection") == env["coll_addr"]
    assert fp.call("treasury_address") == env["treasury"]
    assert fp.call("price") == PRICE
    assert fp.call("supply_cap") == 1000
    assert fp.call("minted_total") == 0
    assert fp.call("remaining") == 1000
    assert fp.call("phase") == b"PREVIEW"


def test_init_rejects_zero_supply():
    reset_registry()
    fp = deploy(FP_PATH, addr("fp"))
    o = addr("o")
    with assert_reverts("bad_supply_cap"):
        fp.call("init", o, o, o, 1, 0, caller=o)


# ── phase gating ────────────────────────────────────────────────────────────


def test_mint_blocked_in_preview(env):
    with assert_reverts("sale_not_open"):
        env["fp"].call("mint", caller=env["alice"], value=PRICE)


def test_set_phase_emits_event(env):
    fp = env["fp"]
    fp.call("set_phase", b"WHITELIST", caller=env["owner"])
    pc = events_named(fp, b"PhaseChanged")
    # init emitted one, set_phase emits another
    assert pc[-1]["args"]["new"] == b"WHITELIST"


def test_only_owner_can_change_phase(env):
    with assert_reverts("not_owner"):
        env["fp"].call("set_phase", b"PUBLIC", caller=env["alice"])


def test_bad_phase_rejected(env):
    with assert_reverts("bad_phase"):
        env["fp"].call("set_phase", b"BURNING", caller=env["owner"])


# ── whitelist ──────────────────────────────────────────────────────────────


def test_whitelist_phase_requires_membership(env):
    fp = env["fp"]
    fp.call("set_phase", b"WHITELIST", caller=env["owner"])
    with assert_reverts("not_whitelisted"):
        fp.call("mint", caller=env["alice"], value=PRICE)


def test_add_then_mint_in_whitelist(env):
    fp = env["fp"]
    added = fp.call("add_whitelist", [env["alice"]], caller=env["owner"])
    assert added == 1
    assert fp.call("is_whitelisted", env["alice"]) is True
    fp.call("set_phase", b"WHITELIST", caller=env["owner"])
    tid = fp.call("mint", caller=env["alice"], value=PRICE)
    assert tid == 1
    assert env["coll"].call("owner_of", 1) == env["alice"]


def test_remove_whitelist_blocks_subsequent_mint(env):
    fp = env["fp"]
    fp.call("add_whitelist", [env["alice"]], caller=env["owner"])
    fp.call("remove_whitelist", [env["alice"]], caller=env["owner"])
    fp.call("set_phase", b"WHITELIST", caller=env["owner"])
    with assert_reverts("not_whitelisted"):
        fp.call("mint", caller=env["alice"], value=PRICE)


# ── public mint ─────────────────────────────────────────────────────────────


def test_public_mint_success(env):
    fp = env["fp"]
    fp.call("set_phase", b"PUBLIC", caller=env["owner"])
    tid = fp.call("mint", caller=env["alice"], value=PRICE)
    assert tid == 1
    assert fp.call("minted_total") == 1
    assert fp.call("remaining") == 999
    assert env["coll"].call("owner_of", 1) == env["alice"]
    # Proceeds went to treasury.
    assert fp.balances.get(env["treasury"], 0) == PRICE
    assert fp.balances.get(env["fp_addr"], 0) == 0


def test_mint_emits_PassMinted(env):
    fp = env["fp"]
    fp.call("set_phase", b"PUBLIC", caller=env["owner"])
    fp.call("mint", caller=env["alice"], value=PRICE)
    minted = events_named(fp, b"PassMinted")
    assert len(minted) == 1
    assert minted[0]["args"]["buyer"] == env["alice"]
    assert minted[0]["args"]["price_paid"] == PRICE
    assert minted[0]["args"]["token_id"] == 1


def test_one_pass_per_wallet(env):
    fp = env["fp"]
    fp.call("set_phase", b"PUBLIC", caller=env["owner"])
    fp.call("mint", caller=env["alice"], value=PRICE)
    with assert_reverts("already_minted"):
        fp.call("mint", caller=env["alice"], value=PRICE)


def test_mint_wrong_value_reverts(env):
    fp = env["fp"]
    fp.call("set_phase", b"PUBLIC", caller=env["owner"])
    with assert_reverts("bad_value_attached"):
        fp.call("mint", caller=env["alice"], value=PRICE - 1)


def test_mint_zero_value_reverts(env):
    fp = env["fp"]
    fp.call("set_phase", b"PUBLIC", caller=env["owner"])
    with assert_reverts("bad_value_attached"):
        fp.call("mint", caller=env["alice"], value=0)


# ── sold-out auto-flip ──────────────────────────────────────────────────────


def test_phase_flips_to_sold_out_on_last_mint():
    reset_registry()
    owner = addr("owner")
    treasury = addr("treasury")
    coll_addr = addr("c")
    fp_addr = addr("f")

    coll = deploy(COLLECTION_PATH, coll_addr)
    coll.call("init", b"X", b"X", owner, b"", 2, owner, 0, caller=owner)

    fp = deploy(FP_PATH, fp_addr)
    fp.call("init", owner, coll_addr, treasury, PRICE, 2, caller=owner)
    coll.call("set_minter", fp_addr, caller=owner)
    fp.call("set_phase", b"PUBLIC", caller=owner)

    fp.call("mint", caller=addr("buyer-a"), value=PRICE)
    assert fp.call("phase") == b"PUBLIC"
    fp.call("mint", caller=addr("buyer-b"), value=PRICE)
    assert fp.call("phase") == b"SOLD_OUT"
    assert fp.call("remaining") == 0

    with assert_reverts("sale_not_open"):
        fp.call("mint", caller=addr("buyer-c"), value=PRICE)


# ── admin: price + treasury ─────────────────────────────────────────────────


def test_set_price_blocked_after_first_mint(env):
    fp = env["fp"]
    fp.call("set_phase", b"PUBLIC", caller=env["owner"])
    fp.call("mint", caller=env["alice"], value=PRICE)
    with assert_reverts("sale_started"):
        fp.call("set_price", PRICE * 2, caller=env["owner"])


def test_set_price_works_during_preview(env):
    fp = env["fp"]
    new_price = PRICE * 2
    fp.call("set_price", new_price, caller=env["owner"])
    assert fp.call("price") == new_price


def test_set_treasury_redirects_proceeds(env):
    fp = env["fp"]
    new_treasury = addr("new-treasury")
    fp.call("set_treasury", new_treasury, caller=env["owner"])
    fp.call("set_phase", b"PUBLIC", caller=env["owner"])
    fp.call("mint", caller=env["alice"], value=PRICE)
    assert fp.balances.get(new_treasury, 0) == PRICE
    assert fp.balances.get(env["treasury"], 0) == 0


# ── batch whitelist limits ──────────────────────────────────────────────────


def test_whitelist_batch_size_limit(env):
    huge = [addr(f"w{i}") for i in range(501)]
    with assert_reverts("bad_batch"):
        env["fp"].call("add_whitelist", huge, caller=env["owner"])


def test_whitelist_dedup_not_double_counted(env):
    fp = env["fp"]
    first = fp.call("add_whitelist", [env["alice"], env["bob"]], caller=env["owner"])
    second = fp.call("add_whitelist", [env["alice"]], caller=env["owner"])
    assert first == 2
    assert second == 0    # alice already on list, no-op
