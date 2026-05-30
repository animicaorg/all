# ANM-721 — Animica NFT standard

Production-ready ERC-721-style non-fungible token contract for the
Animica Python-VM. Used as the base contract for every collection on
the **Animica NFT Marketplace** (animica.xyz/marketplace).

## Why a standard

The marketplace contract (`animica_nft_marketplace`) needs a single
predictable ABI to call into every collection — `owner_of`, `approve`,
`transfer_from`, `royalty_info`. Without a standard, the marketplace
can't safely list a token without a per-collection adapter. ANM-721
fixes that: any contract that exposes the function signatures in
[manifest.json](./manifest.json) is marketplace-compatible.

## Features

- **Token IDs** start at 1 and are monotonically assigned by the
  contract (`next_id()` returns the next mint).
- **Per-token URIs** override an optional collection-wide `base_uri`.
  The marketplace indexer prefers per-token URIs when displaying art.
- **Max supply cap** (`0` means uncapped). Mints past the cap revert
  with `b"max_supply_exceeded"`.
- **Royalties** follow EIP-2981 semantics: `royalty_info(token_id,
  sale_price) → (receiver, royalty_amount)`. The marketplace pays
  this out on every sale. Capped at 10% (1000 bps) so a malicious
  contract owner cannot poison the market.
- **Single-token approvals + operator approvals** so a marketplace
  can be authorised once per holder to transfer any token in the
  collection on their behalf.
- **Designated minter** (e.g. the Founders Pass contract) can be
  set by the owner to delegate public-sale minting without giving
  away contract ownership.

## Public ABI (cheat sheet)

| Function | Mutability | Purpose |
|---|---|---|
| `init(name, symbol, owner, base_uri, max_supply, royalty_receiver, royalty_bps)` | nonpayable | One-time setup |
| `mint(to, uri) → token_id` | nonpayable | Owner or designated minter mints to `to`. `uri` is optional per-token override |
| `transfer_from(from, to, token_id)` | nonpayable | Standard transfer (also `transferFrom` camelCase) |
| `approve(approved, token_id)` | nonpayable | Single-token approval |
| `set_approval_for_all(operator, approved)` | nonpayable | Operator approval — used by the marketplace |
| `burn(token_id)` | nonpayable | Owner/approver destroys a token |
| `owner_of(token_id) → bytes` | view | Current holder |
| `balance_of(addr) → int` | view | How many tokens `addr` holds |
| `token_uri(token_id) → bytes` | view | URI to off-chain metadata JSON |
| `royalty_info(token_id, sale_price) → (receiver, amount)` | view | EIP-2981 royalty for marketplace |
| `set_base_uri(uri)` | nonpayable / owner | Update collection-wide URI prefix |
| `set_token_uri(token_id, uri)` | nonpayable / owner | Override one token's URI |
| `set_minter(addr)` | nonpayable / owner | Authorise a relayer to mint |
| `set_royalty(receiver, bps)` | nonpayable / owner | Update royalty (≤1000 bps) |
| `transfer_ownership(new_owner)` | nonpayable / owner | Hand the contract over |

## Events

`Transfer`, `Approval`, `ApprovalForAll`, `Minted`, `Burned`,
`BaseURIUpdated`, `RoyaltyUpdated`, `OwnershipTransferred`.

The marketplace indexer (`apps/animica-xyz/scripts/marketplace-indexer.ts`)
watches `Transfer` events to keep wallet ↔ NFT ownership in sync,
and `Minted` to populate new NFT rows automatically.

## Storage layout

All keys are byte-prefixed with `anm721:` to avoid collisions with
other contracts on the same state tree. See the docstring in
[contract.py](./contract.py) for the full key list.

## Authorisation matrix

| Action | Allowed for |
|---|---|
| `transfer_from`, `safe_transfer_from` | Token owner, single-token approver, or `setApprovalForAll(operator=true)` operator |
| `approve` | Token owner, or operator with all-approval |
| `mint` | Contract `owner`, or the address set as `minter()` |
| `burn` | Same as transfer (owner / approver / operator) |
| `set_base_uri`, `set_token_uri`, `set_minter`, `set_royalty`, `transfer_ownership` | Contract `owner` only |

The marketplace contract takes the **operator approval** path — a buyer
who clicks "buy" doesn't need to interact with the collection contract,
because the seller already approved the marketplace once.

## Compatibility note: "safe" transfers

ANM-721 exposes `safe_transfer_from` / `safeTransferFrom` for tooling
compatibility, but the Animica Python-VM does not currently have a
contract-recipient callback hook (no `IERC721Receiver`). The "safe"
variants therefore behave identically to `transfer_from` on Animica.

## Tests

See `contracts/tests/test_nft721.py` for the full suite (happy paths,
authorisation matrix, supply cap, approval lifecycle, royalty math).
