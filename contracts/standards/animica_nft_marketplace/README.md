# Animica NFT Marketplace contract

The on-chain core of [animica.xyz/marketplace](https://animica.xyz/marketplace).
Sellers list ANM-721 NFTs; buyers settle in ANM; the contract takes a
marketplace fee, pays creator royalty, and atomically transfers the NFT.

## Pull-style listings

The marketplace does **not** custody NFTs. A seller keeps custody and
authorises the marketplace once per collection:

```py
nft.set_approval_for_all(marketplace_addr, True)
marketplace.list_nft(collection=nft_addr, token_id=42, price=10_000_000_000)
```

If a buyer never arrives, the NFT never moves. Sellers can call
`cancel(listing_id)` at any time. Sellers who transfer the NFT out
without cancelling, or who revoke the operator approval, get an
on-the-spot revert when a buyer tries to settle — there is no stuck
state on the marketplace.

## Fee + royalty split

On `buy(listing_id)` with the listing's `price` attached as the
transaction value:

```
fee     = price * fee_bps / 10000           → fee_recipient
royalty = collection.royalty_info(token_id, price).amount  → royalty_receiver
payout  = price - fee - royalty             → seller
```

The marketplace fee is **capped at 10%** (`MAX_FEE_BPS = 1000`); the
collection's royalty is bounded by ANM-721's own 10% cap. If a buggy
collection returns a royalty > sale_price, the buy reverts with
`b"royalty_too_high"`. If the sum of fee + royalty would exceed price,
the buy reverts with `b"split_overflow"`.

## Reentrancy

The marketplace calls into the collection contract twice on every buy
(`owner_of`, `is_approved_for_all`, `transfer_from`). A malicious
collection could re-enter the marketplace from inside any of those
calls. Mitigations:

1. **Coarse boolean lock** wraps every mutating entry-point. A second
   mutating call inside the same outer tx reverts with `b"reentrant"`.
2. **Checks-effects-interactions** ordering — listing state is set
   to `SOLD` and the reverse index is cleared *before* control returns
   from `transfer_from`. Even if a re-entry slipped past the lock, the
   listing would already be marked unavailable.
3. **Fee math is computed before the external call**, so a re-entry
   can't inflate or zero the split mid-settlement.

## Pause switch

The contract owner can call `set_paused(True)` to disable new
listings and buys. Cancels are still allowed so sellers are never
locked in. This is a circuit breaker for emergencies — a discovered
bug, a chain-wide pause, or a planned migration.

## Storage layout

| Key | Type | Purpose |
|---|---|---|
| `anm_mkt:init` | u1 | Initialized flag |
| `anm_mkt:owner` | bytes | Admin |
| `anm_mkt:fee_recipient` | bytes | Treasury that gets the marketplace fee |
| `anm_mkt:fee_bps` | uint | Fee in basis points (≤ 1000) |
| `anm_mkt:reent` | u1 | Reentrancy guard |
| `anm_mkt:next_listing_id` | uint | Listing counter (1-indexed) |
| `anm_mkt:paused` | u1 | Emergency pause |
| `anm_mkt:l:{id}:collection` | bytes | Listed contract address |
| `anm_mkt:l:{id}:token_id` | uint | Listed token id |
| `anm_mkt:l:{id}:seller` | bytes | Snapshot of seller |
| `anm_mkt:l:{id}:price` | uint | Price (nanos; 1 ANM = 1e9 nanos) |
| `anm_mkt:l:{id}:state` | bytes | ACTIVE / SOLD / CANCELLED |
| `anm_mkt:l:{id}:created_at` | uint | Block timestamp at list time |
| `anm_mkt:by_token:{coll}:{tid}` | uint | Reverse index — current active listing id, 0 if none |

## Events

`Listed(listing_id, collection, token_id, seller, price)`
`Sold(listing_id, buyer, fee, royalty, seller_proceeds, royalty_receiver)`
`Cancelled(listing_id, seller)`
`FeeUpdated(new_bps)`
`Paused(paused)`

The indexer at `apps/animica-xyz/scripts/marketplace-indexer.ts`
watches all five and upserts into the `Listing` / `Sale` Prisma tables
so the marketplace UI can paginate fast.

## Security posture

This contract is the most security-sensitive piece of the marketplace
because it routes ANM. The properties enforced by tests in
`contracts/tests/test_nft_marketplace.py`:

- A listing's seller cannot be impersonated; only the original
  `caller` at list time can `cancel`.
- A listing cannot be sold twice or cancelled twice.
- `buy(listing_id)` reverts unless exactly `price` ANM is attached.
- A buyer who is the seller reverts (no self-sale spoofing).
- `fee + royalty ≤ price` always; rounding goes to the seller.
- A collection whose `royalty_info` returns `> sale_price` causes a
  hard revert rather than draining seller proceeds.
- The reentrancy lock blocks any cross-listing re-entry within a single tx.
- Pausing disables `list_nft` and `buy` but leaves `cancel` reachable.

## What is *not* in scope yet

Out of scope for v1 (planned follow-ups):

- **English auctions** — separate contract, doesn't share state with
  fixed-price listings.
- **Make-offer flow** — buyers can't yet bid below ask. Adds an
  `Offers` table + signed off-chain offers redeemed on-chain.
- **Bundles** — selling a basket of NFTs in one listing.
- **Whitelist / private sales** — Founders Pass exposes this directly
  inside its own contract.
