"""Tests for the Animica NFT Marketplace contract.

Exercises the cross-contract flow against a real ANM-721 collection
deployed in the same harness registry, so transfers actually move
ownership and royalty_info round-trips work.
"""

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
MARKETPLACE_PATH = "contracts/standards/animica_nft_marketplace/contract.py"

NANOS_PER_ANM = 1_000_000_000
PRICE = 10 * NANOS_PER_ANM      # 10 ANM


@pytest.fixture
def env():
    reset_registry()
    owner = addr("owner")
    seller = addr("seller")
    buyer = addr("buyer")
    creator = addr("creator")
    treasury = addr("treasury")
    coll_addr = addr("coll")
    mkt_addr = addr("mkt")

    # Collection
    coll = deploy(COLLECTION_PATH, coll_addr)
    coll.call(
        "init",
        b"Animica Art",
        b"AART",
        owner,
        b"https://meta.animica.xyz/art/",
        0,                # uncapped
        creator,          # royalty receiver
        250,              # 2.5% royalty
        caller=owner,
    )

    # Marketplace
    mkt = deploy(MARKETPLACE_PATH, mkt_addr)
    mkt.call("init", owner, treasury, 250, caller=owner)   # 2.5% mkt fee

    # Mint one NFT to the seller.
    coll.call("mint", seller, b"", caller=owner)

    # Seller approves the marketplace as an operator (one-time).
    coll.call("set_approval_for_all", mkt_addr, True, caller=seller)

    return {
        "coll": coll,
        "mkt": mkt,
        "coll_addr": coll_addr,
        "mkt_addr": mkt_addr,
        "owner": owner,
        "seller": seller,
        "buyer": buyer,
        "creator": creator,
        "treasury": treasury,
    }


# ── init ────────────────────────────────────────────────────────────────────


def test_init_sets_admin_and_fee(env):
    mkt = env["mkt"]
    assert mkt.call("owner") == env["owner"]
    assert mkt.call("fee_recipient") == env["treasury"]
    assert mkt.call("fee_bps") == 250
    assert mkt.call("paused") is False
    assert mkt.call("next_listing_id") == 1


def test_init_caps_fee_at_10pct():
    reset_registry()
    c = deploy(MARKETPLACE_PATH, addr("bad-fee-mkt"))
    owner = addr("o")
    with assert_reverts("bad_fee_bps"):
        c.call("init", owner, owner, 1500, caller=owner)


# ── listing ────────────────────────────────────────────────────────────────


def test_list_emits_event_and_records_state(env):
    mkt = env["mkt"]
    lid = mkt.call(
        "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
    )
    assert lid == 1
    listed = events_named(mkt, b"Listed")
    assert len(listed) == 1
    assert listed[0]["args"]["seller"] == env["seller"]
    assert listed[0]["args"]["price"] == PRICE
    # Reverse index
    assert mkt.call("active_listing_for_token", env["coll_addr"], 1) == 1
    collection, token_id, seller, price, state, _ = mkt.call("get_listing", 1)
    assert collection == env["coll_addr"]
    assert token_id == 1
    assert seller == env["seller"]
    assert price == PRICE
    assert state == b"ACTIVE"


def test_list_requires_token_ownership(env):
    with assert_reverts("not_token_owner"):
        env["mkt"].call(
            "list_nft", env["coll_addr"], 1, PRICE, caller=env["buyer"]
        )


def test_list_requires_operator_approval(env):
    # Use a fresh NFT id 2 minted to a wallet that hasn't approved.
    new_seller = addr("never-approved")
    env["coll"].call("mint", new_seller, b"", caller=env["owner"])
    with assert_reverts("not_approved_for_all"):
        env["mkt"].call(
            "list_nft", env["coll_addr"], 2, PRICE, caller=new_seller
        )


def test_no_double_active_listing(env):
    env["mkt"].call(
        "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
    )
    with assert_reverts("already_listed"):
        env["mkt"].call(
            "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
        )


def test_relist_after_cancel_works(env):
    mkt = env["mkt"]
    lid1 = mkt.call(
        "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
    )
    mkt.call("cancel", lid1, caller=env["seller"])
    lid2 = mkt.call(
        "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
    )
    assert lid2 == lid1 + 1
    assert mkt.call("active_listing_for_token", env["coll_addr"], 1) == lid2


# ── cancel ─────────────────────────────────────────────────────────────────


def test_seller_can_cancel(env):
    mkt = env["mkt"]
    mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])
    mkt.call("cancel", 1, caller=env["seller"])
    _, _, _, _, state, _ = mkt.call("get_listing", 1)
    assert state == b"CANCELLED"
    assert mkt.call("active_listing_for_token", env["coll_addr"], 1) == 0


def test_non_seller_cannot_cancel(env):
    env["mkt"].call(
        "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
    )
    with assert_reverts("not_seller"):
        env["mkt"].call("cancel", 1, caller=env["buyer"])


def test_cancel_twice_reverts(env):
    env["mkt"].call(
        "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
    )
    env["mkt"].call("cancel", 1, caller=env["seller"])
    with assert_reverts("not_active"):
        env["mkt"].call("cancel", 1, caller=env["seller"])


# ── buy + fee split ─────────────────────────────────────────────────────────


def test_buy_settles_fee_royalty_and_seller_payout(env):
    mkt = env["mkt"]
    mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])

    # PRICE = 10 ANM; fee = 2.5% = 0.25 ANM; royalty = 2.5% = 0.25 ANM;
    # seller payout = 9.5 ANM.
    mkt.call("buy", 1, caller=env["buyer"], value=PRICE)

    assert mkt.balances.get(env["treasury"], 0) == PRICE // 40    # 2.5%
    assert mkt.balances.get(env["creator"], 0) == PRICE // 40     # 2.5%
    assert mkt.balances.get(env["seller"], 0) == PRICE - 2 * (PRICE // 40)
    # Marketplace's own ANM balance returns to zero.
    assert mkt.balances.get(env["mkt_addr"], 0) == 0
    # NFT moved.
    assert env["coll"].call("owner_of", 1) == env["buyer"]
    # Listing marked sold.
    _, _, _, _, state, _ = mkt.call("get_listing", 1)
    assert state == b"SOLD"
    # Reverse index cleared.
    assert mkt.call("active_listing_for_token", env["coll_addr"], 1) == 0
    # Sold event has the correct split.
    sold = events_named(mkt, b"Sold")
    assert len(sold) == 1
    assert sold[0]["args"]["buyer"] == env["buyer"]
    assert sold[0]["args"]["fee"] == PRICE // 40
    assert sold[0]["args"]["royalty"] == PRICE // 40
    assert sold[0]["args"]["seller_proceeds"] == PRICE - 2 * (PRICE // 40)


def test_buy_with_wrong_value_reverts(env):
    env["mkt"].call(
        "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
    )
    with assert_reverts("bad_value_attached"):
        env["mkt"].call("buy", 1, caller=env["buyer"], value=PRICE - 1)


def test_buy_after_cancel_reverts(env):
    env["mkt"].call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])
    env["mkt"].call("cancel", 1, caller=env["seller"])
    with assert_reverts("not_active"):
        env["mkt"].call("buy", 1, caller=env["buyer"], value=PRICE)


def test_buy_after_sold_reverts(env):
    mkt = env["mkt"]
    mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])
    mkt.call("buy", 1, caller=env["buyer"], value=PRICE)
    with assert_reverts("not_active"):
        mkt.call("buy", 1, caller=env["buyer"], value=PRICE)


def test_self_buy_reverts(env):
    env["mkt"].call(
        "list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"]
    )
    with assert_reverts("buyer_is_seller"):
        env["mkt"].call("buy", 1, caller=env["seller"], value=PRICE)


def test_buy_fails_when_seller_no_longer_holds(env):
    mkt = env["mkt"]
    mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])
    # Seller transfers the NFT away without cancelling the listing.
    env["coll"].call(
        "transfer_from", env["seller"], addr("third"), 1, caller=env["seller"]
    )
    with assert_reverts("seller_no_longer_holder"):
        mkt.call("buy", 1, caller=env["buyer"], value=PRICE)


def test_buy_fails_when_approval_revoked(env):
    mkt = env["mkt"]
    mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])
    env["coll"].call(
        "set_approval_for_all", env["mkt_addr"], False, caller=env["seller"]
    )
    with assert_reverts("approval_revoked"):
        mkt.call("buy", 1, caller=env["buyer"], value=PRICE)


# ── admin ──────────────────────────────────────────────────────────────────


def test_pause_blocks_list_and_buy(env):
    mkt = env["mkt"]
    mkt.call("set_paused", True, caller=env["owner"])
    with assert_reverts("paused"):
        mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])
    # Unpause and confirm listing works again.
    mkt.call("set_paused", False, caller=env["owner"])
    mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])


def test_cancel_still_works_when_paused(env):
    mkt = env["mkt"]
    mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])
    mkt.call("set_paused", True, caller=env["owner"])
    mkt.call("cancel", 1, caller=env["seller"])


def test_only_owner_can_change_fee(env):
    with assert_reverts("not_owner"):
        env["mkt"].call("set_fee_bps", 500, caller=env["buyer"])


def test_fee_cap_enforced(env):
    with assert_reverts("bad_fee_bps"):
        env["mkt"].call("set_fee_bps", 1500, caller=env["owner"])


def test_set_fee_updates_subsequent_sales(env):
    mkt = env["mkt"]
    mkt.call("set_fee_bps", 500, caller=env["owner"])     # bump to 5%
    mkt.call("list_nft", env["coll_addr"], 1, PRICE, caller=env["seller"])
    mkt.call("buy", 1, caller=env["buyer"], value=PRICE)
    assert mkt.balances.get(env["treasury"], 0) == PRICE * 5 // 100
