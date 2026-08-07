# paid-api (deployed as `anm-toolkit`)

An ANM money-math utility API deployed **with a per-call surcharge** — the simplest way a
developer earns on Animica Python Cloud.

## What it demonstrates

* **`perCallNanm`** — this function is deployed with a 5,000,000 nANM (0.005 ANM) per-call
  surcharge. Every successful execution's price is:

  `price = base + cpu + memory + egress (+ AI/GPU) + 5,000,000 nANM surcharge`

* **The earnings split** — the caller's payment is divided exactly
  (`price == platformFee + developer + provider`, integer nANM, developer takes the
  remainder):
  * platform fee: `feeBps` (20% standard, 10% for Founding Developers) → treasury
  * developer: the remainder → the owner's spendable ledger balance, same transaction
* **Failure pricing** — a failed run charges metered resource cost only, with **no**
  surcharge: the developer earns nothing from a call that did not succeed.
* **Auth economics** — a function with a surcharge cannot be invoked anonymously; the caller
  must be an authenticated account that can actually be charged (`401 auth_required`
  otherwise).
* Exact-integer money math in Python (`int` is arbitrary-precision): ANM↔nANM conversion,
  bech32m address shape checks, and the same floor-and-remainder split the platform's
  pricing engine uses.

## Deploy configuration (set by `scripts/cloud-examples.ts`)

| setting | value |
| --- | --- |
| entrypoint | `main` |
| timeout | 10 000 ms |
| memory | 128 MB |
| capabilities | none |
| per-call surcharge | 5 000 000 nANM (0.005 ANM) |

## Invoke (requires an API key)

```bash
curl -s https://animica.dev/api/cloud/v1/fn/examples/anm-toolkit \
  -H "authorization: Bearer $ANM_KEY" \
  -H 'content-type: application/json' \
  -d '{"op": "split", "amount_nanm": "250000000", "fee_bps": 2000}'
```

Response:

```json
{"op": "split", "total_nanm": "250000000", "platform_fee_nanm": "50000000",
 "developer_nanm": "200000000", "provider_nanm": "0", "check_exact_sum": true}
```

Other operations:

```json
{"op": "convert", "anm": "1.25"}
{"op": "convert", "nanm": "1250000000"}
{"op": "validate_address", "address": "anim1..."}
```
