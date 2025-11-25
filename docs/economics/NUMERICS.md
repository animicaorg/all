# Economics Numerics — Worked Examples & Calculator Notes (v1)

This note collects **concrete formulas, rounding rules, and worked examples** for fees, rewards, issuance, and AICF payouts. It complements:
- `docs/economics/FEES.md`, `REWARDS.md`, `INFLATION.md`, `MEV.md`, `SLASHING.md`
- runtime sources: `mempool/fee_market.py`, `execution/runtime/fees.py`, `aicf/economics/*`
- chain parameters: `spec/params.yaml`

> **Notation:** All monetary math is done in **base units** with integer arithmetic. Let:
> - `D`: decimals for ANM (from chain params; default **18**).  
> - `1 ANM = 10^D aN` (e.g., **atto-ANM** if `D=18`).
> - `τ`: target block time (s).  
> - `E`: epoch length (blocks) for AICF settlement.  
> - `ceil_div(x, y) = ⌈x / y⌉`, `floor_div(x, y) = ⌊x / y⌋`.  
> - Saturating math: never allow negative balances.

---

## 1) Time, Windows, and Units

- Blocks/day: `B_day = floor_div(86400, τ)`
- Blocks/year: `B_year = floor_div(365*86400, τ)`
- Epochs/year: `Ep_year = floor_div(B_year, E)`

Use these for budget planning (issuance, treasury drips, AICF caps).

---

## 2) Fees (Tx & Blob)

### 2.1 Gas Price Decomposition
`effective_gas_price = max(min_gas_price, floor_price * surge_mult) + tip`

- `min_gas_price`: policy floor (from `mempool/config.py` / fee-market EMA)
- `floor_price`: dynamic observed base (EMA of recent blocks)
- `surge_mult`: ≥ 1 in congestion (bounded)
- `tip`: sender-chosen priority fee (bounded)

**Tx fee (aN)**:

tx_fee = gas_used * effective_gas_price

Round **up** when computing `effective_gas_price` from fractional policies to avoid under-collection:

effective_gas_price = ceil_div( floor_price * surge_num , surge_den ) + tip

### 2.2 Intrinsic Gas & Size
See `execution/gas/intrinsic.py`. A transfer example:

gas_intrinsic = GAS_TX_BASE + GAS_ACCESS_LISTk + GAS_PAYLOAD_BYTElen(data)

Total `gas_used = gas_intrinsic + execution_gas`.

### 2.3 Blob Fees (if DA used by the tx)
Let `blob_size_bytes`, `price_per_kiB` (aN/kiB), and `overhead = kiB`:

blob_kiB = ceil_div(blob_size_bytes, 1024)
blob_fee  = (blob_kiB + overhead) * price_per_kiB

Total fee due:

total_fee = tx_fee + blob_fee

---

## 3) Block Rewards & Splits

Let `R_block` be the minted reward per block (aN). Split by policy:

to_miner    = floor_div( R_block * α_miner_num    , α_den )
to_treasury = floor_div( R_block * α_treasury_num , α_den )
to_aicf     = R_block - to_miner - to_treasury   # remainder to AICF

- α are rational weights with common denominator `α_den` to keep integer math.
- Any rounding remainder stays with AICF by construction.

**Fees** split:

base_fee_burn = floor_div(tx_fee * β_burn_num, β_den)
miner_tip     = tx_tip_sum
treasury_cut  = tx_fee - base_fee_burn - miner_tip

(Exact policy depends on `execution/runtime/fees.py`.)

---

## 4) Issuance Schedules

### 4.1 Constant Rate (per year)
Let `rate_ppm` be parts-per-million annual rate on circulating supply `S_circ`:

I_annual  = floor_div( S_circ * rate_ppm , 1_000_000 )
I_block   = floor_div( I_annual , B_year )

### 4.2 Halving (Bitcoin-like)
Let `R0` be initial per-block reward, halving every `H` blocks at heights `k*H`:

R_block(height) = floor_div(R0 , 2^k)

Cap issuance at `I_cap` using saturating accumulation.

---

## 5) AICF Pricing & Settlement

Let `ai_units` or `quantum_units` be rated usage units from verified proofs; price schedules:

price_ai_per_unit      (aN)
price_quantum_per_unit (aN)
reward_ai      = ai_units      * price_ai_per_unit
reward_quantum = quantum_units * price_quantum_per_unit

Epoch aggregation:

R_epoch_provider = Σ(reward_ai + reward_quantum) over jobs in epoch

Apply slashes/clawbacks per `SLASHING.md` at epoch close:

payout_after = max(0, R_epoch_provider - clawback)
stake_burn   = slash_amount - clawback   # bounded by stake

---

## 6) Worked Examples

### Example A — Transfer Fee
- `gas_used = 21_000`
- `floor_price = 3_000 aN/gas`
- `surge_mult = 1.25`  → `surge_num=5, surge_den=4`
- `tip = 500 aN/gas`, `min_gas_price = 2_000 aN/gas`

Compute:

effective_gas_price = max(2_000, ceil_div(3_000*5,4)) + 500
= max(2_000, ceil_div(15_000,4)) + 500
= max(2_000, 3_750) + 500
= 4_250 aN/gas

tx_fee = 21_000 * 4_250 = 89_250_000 aN

If `D=18`, that's `0.00008925 ANM`.

### Example B — Blob Fee
- `blob_size = 150_000 bytes`, `price_per_kiB = 80_000 aN/kiB`, `overhead=4`

blob_kiB = ceil_div(150_000,1024) = 147
blob_fee = (147 + 4) * 80_000 = 12_080_000 aN

Total fee with Example A:

total = 89_250_000 + 12_080_000 = 101_330_000 aN

### Example C — Block Reward Split
- `R_block = 5_000_000_000 aN` (0.005 ANM)
- Splits: miner 40%, treasury 40%, AICF 20% → α_den=100

to_miner    = floor_div(5e9 * 40, 100) = 2_000_000_000
to_treasury = floor_div(5e9 * 40, 100) = 2_000_000_000
to_aicf     = 1_000_000_000

### Example D — Annual Issuance (Constant Rate)
- `S_circ = 100_000_000 ANM = 1e8 * 10^18 aN`
- `rate_ppm = 20_000` (2.0%/yr), `τ=2s` → `B_year=15_768_000`

I_annual = floor_div( S_circ_aN * 20_000, 1_000_000 ) = S_circ_aN * 0.02
I_block  = floor_div( I_annual, 15_768_000 )

(Keep in base units; display human-readable at UI.)

### Example E — AICF Job Rewards & Slash
- For an epoch, provider reports: `ai_units=1200`, `quantum_units=35`
- Prices: `ai=300_000 aN/unit`, `quantum=20_000_000 aN/unit`

reward_ai      = 1200 * 300_000 = 360_000_000 aN
reward_quantum =   35 * 20_000_000 = 700_000_000 aN
R_epoch        = 1_060_000_000 aN

If SLA yields `clawback=100_000_000 aN`, `slash=250_000_000 aN`:

payout_after = max(0, 1_060_000_000 - 100_000_000) = 960_000_000
stake_burn   = 250_000_000 - 100_000_000 = 150_000_000 aN

---

## 7) Rounding & Safety Rules

1. **Integer-only**: store and compute in base units.
2. **Ceil for charges**, **floor for payouts** where ambiguity exists (conservative).
3. **Saturate** on underflow: never negative balances.
4. **Bound tips/surge** by policy to prevent overflow; use 128-bit or big-int.
5. **Deterministic order**: apply clawbacks (fees) before stake burns (security).

Reference helpers:
- `core/utils/bytes.py` (safe ints/hex),  
- `execution/types/gas.py` (Gas, GasPrice),  
- `mempool/fee_market.py` (EMA, surge),  
- `aicf/economics/settlement.py` (epoch math).

---

## 8) Calculator Snippets

### 8.1 Gas/Blob Fee (Python-like pseudocode)
```py
def ceil_div(x, y): return (x + y - 1) // y

def effective_gas_price(min_g, floor_g, surge_num, surge_den, tip):
    surged = ceil_div(floor_g * surge_num, surge_den)
    return max(min_g, surged) + tip

def tx_total_fee(gas_used, min_g, floor_g, surge_num, surge_den, tip,
                 blob_bytes=0, price_per_kib=0, overhead_kib=0):
    egp = effective_gas_price(min_g, floor_g, surge_num, surge_den, tip)
    tx_fee = gas_used * egp
    if blob_bytes == 0:
        return tx_fee
    kib = ceil_div(blob_bytes, 1024)
    blob_fee = (kib + overhead_kib) * price_per_kib
    return tx_fee + blob_fee

8.2 Reward Split

def split_reward(R_block, a_miner, a_treasury, a_den):
    to_miner    = (R_block * a_miner) // a_den
    to_treasury = (R_block * a_treasury) // a_den
    to_aicf     = R_block - to_miner - to_treasury
    return to_miner, to_treasury, to_aicf

8.3 Constant-Rate Issuance

def issuance_per_block(S_circ, rate_ppm, blocks_per_year):
    I_annual = (S_circ * rate_ppm) // 1_000_000
    return I_annual // blocks_per_year


⸻

9) Display & UX
	•	Always render amounts in ANM with D decimals, but copy/export base units for programmatic use.
	•	Show fee breakdown (base, tip, blob) and reward split (miner/treasury/AICF).
	•	Provide epoch receipts to providers: pre/post balances, slashes, evidence hashes.

⸻

10) Regression Tests (Suggested)
	•	Fee invariants around surge boundaries (off-by-one).
	•	Blob fee rounding for sizes near KiB boundaries.
	•	Reward split sums to R_block for all α.
	•	Issuance year-over-year within 1 aN drift (pure integer).
	•	Settlement order: clawback then burn; no negative balances.

⸻

Summary

This guide pinches ambiguity out of economic math by standardizing integer-only, rounding-aware, and deterministic calculations with concrete examples. Implementations in mempool, execution, and AICF should match these rules bit-for-bit to ensure reproducibility across nodes and SDKs.

Version: v1.0. Parameters subject to governance updates; pin via chain params and policy hashes.
