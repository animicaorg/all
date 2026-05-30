"""Tests for the ANM-721 NFT standard contract."""

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


@pytest.fixture
def env():
    reset_registry()
    owner = addr("owner")
    alice = addr("alice")
    bob = addr("bob")
    carol = addr("carol")
    collection_addr = addr("nft-collection")
    contract = deploy(COLLECTION_PATH, collection_addr)
    contract.call(
        "init",
        b"Animica Founders",
        b"AFP",
        owner,
        b"https://meta.animica.xyz/founders/",
        100,                  # max supply
        owner,                # royalty receiver
        500,                  # 5% royalty
        caller=owner,
    )
    return {
        "contract": contract,
        "owner": owner,
        "alice": alice,
        "bob": bob,
        "carol": carol,
    }


# ── init ────────────────────────────────────────────────────────────────────


def test_init_sets_metadata(env):
    c = env["contract"]
    assert c.call("name") == b"Animica Founders"
    assert c.call("symbol") == b"AFP"
    assert c.call("owner") == env["owner"]
    assert c.call("base_uri") == b"https://meta.animica.xyz/founders/"
    assert c.call("max_supply") == 100
    assert c.call("total_supply") == 0
    assert c.call("next_id") == 1


def test_init_twice_reverts(env):
    with assert_reverts("already_initialized"):
        env["contract"].call(
            "init",
            b"X", b"X", env["owner"], b"", 0, env["owner"], 0,
            caller=env["owner"],
        )


def test_init_rejects_excessive_royalty():
    reset_registry()
    c = deploy(COLLECTION_PATH, addr("bad-royalty"))
    owner = addr("owner")
    with assert_reverts("bad_royalty_bps"):
        c.call(
            "init",
            b"X", b"X", owner, b"", 0, owner, 1001,
            caller=owner,
        )


# ── mint ────────────────────────────────────────────────────────────────────


def test_mint_assigns_increasing_ids(env):
    c = env["contract"]
    tid1 = c.call("mint", env["alice"], b"", caller=env["owner"])
    tid2 = c.call("mint", env["bob"], b"", caller=env["owner"])
    assert tid1 == 1
    assert tid2 == 2
    assert c.call("owner_of", tid1) == env["alice"]
    assert c.call("owner_of", tid2) == env["bob"]
    assert c.call("total_supply") == 2
    assert c.call("balance_of", env["alice"]) == 1
    assert c.call("balance_of", env["bob"]) == 1


def test_mint_emits_Transfer_and_Minted(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"ipfs://cat.json", caller=env["owner"])
    minted = events_named(c, b"Minted")
    assert len(minted) == 1
    assert minted[0]["args"]["to"] == env["alice"]
    assert minted[0]["args"]["uri"] == b"ipfs://cat.json"
    transfers = events_named(c, b"Transfer")
    assert any(t["args"]["from"] == b"" and t["args"]["to"] == env["alice"] for t in transfers)


def test_non_owner_cannot_mint(env):
    with assert_reverts("not_mint_authority"):
        env["contract"].call("mint", env["bob"], b"", caller=env["alice"])


def test_designated_minter_can_mint(env):
    c = env["contract"]
    relayer = addr("relayer")
    c.call("set_minter", relayer, caller=env["owner"])
    tid = c.call("mint", env["alice"], b"", caller=relayer)
    assert tid == 1
    assert c.call("owner_of", 1) == env["alice"]


def test_mint_respects_max_supply():
    reset_registry()
    owner = addr("owner")
    alice = addr("alice")
    c = deploy(COLLECTION_PATH, addr("small"))
    c.call("init", b"X", b"X", owner, b"", 2, owner, 0, caller=owner)
    c.call("mint", alice, b"", caller=owner)
    c.call("mint", alice, b"", caller=owner)
    with assert_reverts("max_supply_exceeded"):
        c.call("mint", alice, b"", caller=owner)


def test_token_uri_per_token_overrides_base(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"ipfs://custom.json", caller=env["owner"])
    c.call("mint", env["alice"], b"", caller=env["owner"])
    assert c.call("token_uri", 1) == b"ipfs://custom.json"
    assert c.call("token_uri", 2) == b"https://meta.animica.xyz/founders/2"


# ── transfer / approval ─────────────────────────────────────────────────────


def test_transfer_from_by_owner(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"", caller=env["owner"])
    c.call("transfer_from", env["alice"], env["bob"], 1, caller=env["alice"])
    assert c.call("owner_of", 1) == env["bob"]
    assert c.call("balance_of", env["alice"]) == 0
    assert c.call("balance_of", env["bob"]) == 1


def test_transfer_from_clears_approval(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"", caller=env["owner"])
    c.call("approve", env["bob"], 1, caller=env["alice"])
    assert c.call("get_approved", 1) == env["bob"]
    c.call("transfer_from", env["alice"], env["bob"], 1, caller=env["alice"])
    assert c.call("get_approved", 1) == b""


def test_unauthorized_transfer_reverts(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"", caller=env["owner"])
    with assert_reverts("caller_not_approved"):
        c.call("transfer_from", env["alice"], env["bob"], 1, caller=env["bob"])


def test_approve_then_third_party_transfer(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"", caller=env["owner"])
    c.call("approve", env["carol"], 1, caller=env["alice"])
    c.call("transfer_from", env["alice"], env["bob"], 1, caller=env["carol"])
    assert c.call("owner_of", 1) == env["bob"]


def test_operator_approval_lets_marketplace_transfer(env):
    c = env["contract"]
    marketplace = addr("marketplace")
    c.call("mint", env["alice"], b"", caller=env["owner"])
    c.call("set_approval_for_all", marketplace, True, caller=env["alice"])
    assert c.call("is_approved_for_all", env["alice"], marketplace) is True
    c.call("transfer_from", env["alice"], env["bob"], 1, caller=marketplace)
    assert c.call("owner_of", 1) == env["bob"]


def test_self_approval_for_all_rejected(env):
    with assert_reverts("self_approval"):
        env["contract"].call(
            "set_approval_for_all", env["alice"], True, caller=env["alice"]
        )


# ── burn ────────────────────────────────────────────────────────────────────


def test_burn_by_owner(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"", caller=env["owner"])
    c.call("burn", 1, caller=env["alice"])
    assert c.call("total_supply") == 0
    assert c.call("balance_of", env["alice"]) == 0
    with assert_reverts("nonexistent_token"):
        c.call("owner_of", 1)


def test_burn_by_non_owner_reverts(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"", caller=env["owner"])
    with assert_reverts("caller_not_approved"):
        c.call("burn", 1, caller=env["bob"])


# ── royalty ────────────────────────────────────────────────────────────────


def test_royalty_info_returns_5_percent(env):
    c = env["contract"]
    c.call("mint", env["alice"], b"", caller=env["owner"])
    receiver, amount = c.call("royalty_info", 1, 1_000_000_000)    # 1 ANM
    assert receiver == env["owner"]
    assert amount == 50_000_000     # 5% of 1 ANM


def test_set_royalty_updates_values(env):
    c = env["contract"]
    new_receiver = addr("royalty-new")
    c.call("set_royalty", new_receiver, 250, caller=env["owner"])
    c.call("mint", env["alice"], b"", caller=env["owner"])
    receiver, amount = c.call("royalty_info", 1, 10_000)
    assert receiver == new_receiver
    assert amount == 250


def test_set_royalty_caps_at_10pct(env):
    with assert_reverts("bad_royalty_bps"):
        env["contract"].call("set_royalty", env["owner"], 1500, caller=env["owner"])


# ── admin ──────────────────────────────────────────────────────────────────


def test_transfer_ownership(env):
    c = env["contract"]
    new_owner = addr("new-owner")
    c.call("transfer_ownership", new_owner, caller=env["owner"])
    assert c.call("owner") == new_owner
    # The old owner can no longer mint (unless also designated as minter).
    with assert_reverts("not_mint_authority"):
        c.call("mint", env["alice"], b"", caller=env["owner"])


def test_set_base_uri_updates_view(env):
    c = env["contract"]
    c.call("set_base_uri", b"https://new.example/", caller=env["owner"])
    c.call("mint", env["alice"], b"", caller=env["owner"])
    assert c.call("token_uri", 1) == b"https://new.example/1"
