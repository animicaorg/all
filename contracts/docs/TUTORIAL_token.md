# Tutorial: Build & Ship a Fungible Token (Animica-20)

This step-by-step guide walks you from **zero → deployed token** on a devnet/testnet using the deterministic Python-VM, the token stdlib, and the provided tooling.

You will:
1) Scaffold a simple **Animica-20** token contract,
2) Add **mint/burn** and **permit** (off-chain approvals w/ PQ signatures),
3) **Lint → Compile → Package → Deploy → Verify**,
4) Run **local tests** and call the contract via CLI/SDK.

> The stdlib gives you production-ready building blocks under `contracts/stdlib/token/*` plus access/controls in `contracts/stdlib/{access,control,math}`. You compose these as pure Python functions with strict determinism.

---

## 0) Prereqs

- **Python ≥ 3.11**
- (Recommended) **virtualenv**
- A running node (local **devnet** or public **testnet**)
- The repo checked out locally

```bash
cd contracts
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env to set RPC_URL, CHAIN_ID, (optional) FAUCET, DEPLOYER_MNEMONIC

If you’re using the provided devnet: see tests/devnet/docker-compose.yml and the quick scripts under tests/devnet/.

⸻

1) Storage & API Design (what we’ll build)

We’ll implement a minimal Animica-20 token with:
	•	Metadata: name, symbol, decimals
	•	Core ERC20-like interface: totalSupply, balanceOf, transfer, allowance, approve, transferFrom
	•	Extensions: mint, burn (owner or role-gated)
	•	permit: off-chain approvals w/ PQ domain separation
	•	Events: Transfer, Approval, Mint, Burn, plus admin events

We’ll leverage:
	•	contracts/stdlib/token/fungible.py
	•	contracts/stdlib/token/mintable.py
	•	contracts/stdlib/token/permit.py
	•	contracts/stdlib/access/ownable.py (or roles)
	•	contracts/stdlib/math/safe_uint.py (checked/saturating math)
	•	contracts/stdlib/utils/events.py (canonical encoders)

⸻

2) Write the Contract

Create contracts/examples/token/contract.py (a fresh tutorial copy if you want to keep yours separate). Below is a compact, readable composition using the stdlib. You can customize names/roles as needed.

# contracts/examples/token/contract.py
# Deterministic, no external I/O, pure Python-VM allowed subset.

from stdlib.storage import get, set
from stdlib.abi import require, revert
from stdlib.events import emit
from stdlib.math.safe_uint import uadd, usub, ucheck
from stdlib.access.ownable import only_owner_init, only_owner
from stdlib.token.fungible import (
    balance_of, set_balance_of, allowance_of, set_allowance_of,
    total_supply, set_total_supply
)
from stdlib.token.mintable import _mint, _burn
from stdlib.token.permit import permit_approve  # PQ-aware domain-separated permit

# ---- Metadata keys
_K_NAME     = b"meta:name"
_K_SYMBOL   = b"meta:symbol"
_K_DECIMALS = b"meta:decimals"

# ---- Initialization (one-time)
def init(name: bytes, symbol: bytes, decimals: int, owner: bytes):
    """
    Initialize token metadata and owner.

    Args:
      name (bytes)
      symbol (bytes)
      decimals (int)
      owner (address bytes)
    """
    only_owner_init(owner)  # sets owner if not set yet
    require(0 < len(name) <= 64, "bad_name")
    require(0 < len(symbol) <= 16, "bad_symbol")
    require(0 <= decimals <= 36, "bad_decimals")

    set(_K_NAME, name)
    set(_K_SYMBOL, symbol)
    set(_K_DECIMALS, decimals)

    # Ensure clean supply
    set_total_supply(0)

    emit(b"Initialized", {
        "name": name, "symbol": symbol, "decimals": decimals, "owner": owner
    })

# ---- Views
def name() -> bytes:
    return get(_K_NAME) or b""

def symbol() -> bytes:
    return get(_K_SYMBOL) or b""

def decimals() -> int:
    dec = get(_K_DECIMALS)
    return int(dec) if dec is not None else 18

def totalSupply() -> int:
    return total_supply()

def balanceOf(owner: bytes) -> int:
    return balance_of(owner)

def allowance(owner: bytes, spender: bytes) -> int:
    return allowance_of(owner, spender)

# ---- Internal helpers
def _transfer(sender: bytes, to: bytes, amount: int):
    require(to and len(to) == len(sender), "bad_to")
    ucheck(amount >= 0, "bad_amount")

    from_bal = balance_of(sender)
    require(from_bal >= amount, "insufficient_balance")
    to_bal = balance_of(to)

    set_balance_of(sender, usub(from_bal, amount))
    set_balance_of(to, uadd(to_bal, amount))

    emit(b"Transfer", {"from": sender, "to": to, "value": amount})

# ---- Mutations
def transfer(to: bytes, amount: int) -> bool:
    """
    Transfer tokens from msg.sender to 'to'.
    """
    _transfer(msg_sender(), to, amount)  # msg_sender() provided by VM runtime
    return True

def approve(spender: bytes, amount: int) -> bool:
    """
    Set allowance from msg.sender to spender.
    """
    owner = msg_sender()
    ucheck(amount >= 0, "bad_amount")
    set_allowance_of(owner, spender, amount)
    emit(b"Approval", {"owner": owner, "spender": spender, "value": amount})
    return True

def transferFrom(owner: bytes, to: bytes, amount: int) -> bool:
    """
    Spend allowance: move tokens from 'owner' to 'to' using msg.sender as spender.
    """
    spender = msg_sender()
    allowed = allowance_of(owner, spender)
    require(allowed >= amount, "allowance_low")
    _transfer(owner, to, amount)
    set_allowance_of(owner, spender, usub(allowed, amount))
    return True

# ---- Mint / Burn (owner-gated)
def mint(to: bytes, amount: int):
    only_owner()
    ucheck(amount >= 0, "bad_amount")
    _mint(to, amount)  # updates balance and totalSupply, emits Mint/Transfer

def burn(from_addr: bytes, amount: int):
    only_owner()
    ucheck(amount >= 0, "bad_amount")
    _burn(from_addr, amount)  # updates balance and totalSupply, emits Burn/Transfer

# ---- Permit (off-chain approval; PQ-signed)
def permit(owner: bytes, spender: bytes, value: int, nonce: int, deadline: int, signature: bytes) -> bool:
    """
    EIP-2612-like flow adapted to PQ domains. Verifies signature off-chain domain bytes,
    then sets allowance if valid. Nonce/deadline checked inside permit_approve.
    """
    permit_approve(owner, spender, value, nonce, deadline, signature)
    emit(b"Approval", {"owner": owner, "spender": spender, "value": value})
    return True

# ---- VM-provided (imported by runtime sandbox)
def msg_sender() -> bytes:
    # placeholder – provided by the VM runtime during execution
    # In local tests, the harness injects the sender.
    return b""

Notes
	•	only_owner_init(owner) writes owner once on first call.
	•	_mint/_burn enforce supply & event invariants (and call Transfer from/to zero).
	•	permit_approve validates PQ signature & updates allowance.

For a roles-based model, swap only_owner with has_role(ROLE_MINTER) etc. using stdlib/access/roles.py.

⸻

3) Lint (determinism & style)

source .env
python -m contracts.tools.lint_contract contracts/examples/token/contract.py

This checks:
	•	Disallowed imports / builtins
	•	Numeric bounds & recursion limits
	•	Basic style rules (see contracts/CODESTYLE.md)

⸻

4) Manifest

Create a minimal manifest with ABI & metadata (you can also generate ABI from decorators/docstrings via contracts/tools/abi_gen.py if you annotate functions). For this tutorial, use a fixture ABI or craft your own.

Option A (use fixture ABI)
We ship contracts/fixtures/abi/token20.json. Copy it and append mint, burn, permit if needed.

Option B (write a manifest)
Create contracts/examples/token/manifest.json:

{
  "name": "TutorialToken",
  "version": "1.0.0",
  "abi": [
    {"name":"init","inputs":[{"name":"name","type":"bytes"},{"name":"symbol","type":"bytes"},{"name":"decimals","type":"u32"},{"name":"owner","type":"address"}],"outputs":[]},
    {"name":"name","inputs":[],"outputs":[{"type":"bytes"}]},
    {"name":"symbol","inputs":[],"outputs":[{"type":"bytes"}]},
    {"name":"decimals","inputs":[],"outputs":[{"type":"u32"}]},
    {"name":"totalSupply","inputs":[],"outputs":[{"type":"u256"}]},
    {"name":"balanceOf","inputs":[{"name":"owner","type":"address"}],"outputs":[{"type":"u256"}]},
    {"name":"allowance","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"outputs":[{"type":"u256"}]},
    {"name":"transfer","inputs":[{"name":"to","type":"address"},{"name":"amount","type":"u256"}],"outputs":[{"type":"bool"}]},
    {"name":"approve","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"u256"}],"outputs":[{"type":"bool"}]},
    {"name":"transferFrom","inputs":[{"name":"owner","type":"address"},{"name":"to","type":"address"},{"name":"amount","type":"u256"}],"outputs":[{"type":"bool"}]},
    {"name":"mint","inputs":[{"name":"to","type":"address"},{"name":"amount","type":"u256"}],"outputs":[]},
    {"name":"burn","inputs":[{"name":"from","type":"address"},{"name":"amount","type":"u256"}],"outputs":[]},
    {"name":"permit","inputs":[
      {"name":"owner","type":"address"},
      {"name":"spender","type":"address"},
      {"name":"value","type":"u256"},
      {"name":"nonce","type":"u64"},
      {"name":"deadline","type":"u64"},
      {"name":"signature","type":"bytes"}
    ],"outputs":[{"type":"bool"}]}
  ],
  "errors": [],
  "events": [
    {"name":"Initialized","fields":[{"name":"name","type":"bytes"},{"name":"symbol","type":"bytes"},{"name":"decimals","type":"u32"},{"name":"owner","type":"address"}]},
    {"name":"Transfer","fields":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"value","type":"u256"}]},
    {"name":"Approval","fields":[{"name":"owner","type":"address"},{"name":"spender","type":"address"},{"name":"value","type":"u256"}]},
    {"name":"Mint","fields":[{"name":"to","type":"address"},{"name":"value","type":"u256"}]},
    {"name":"Burn","fields":[{"name":"from","type":"address"},{"name":"value","type":"u256"}]}
  ]
}


⸻

5) Build & Package

Compile to IR and produce a deploy bundle with code hash and ABI included:

python -m contracts.tools.build_package \
  --src contracts/examples/token/contract.py \
  --manifest contracts/examples/token/manifest.json \
  --out contracts/build/tutorial_token.pkg.json

Output includes:
	•	Code hash (used for verification / proxy-pinning),
	•	ABI and metadata,
	•	Deterministic IR bytes.

⸻

6) Local Sanity (VM-only)

Quick local calls with the in-repo helper:

python -m contracts.tools.call \
  --local-vm \
  --pkg contracts/build/tutorial_token.pkg.json \
  --fn init \
  --args '["TutorialToken","TT",18,"<OWNER_ADDR_HEX>"]'

Replace <OWNER_ADDR_HEX> with a bech32 or hex address encoded as the ABI expects (see contracts/stdlib/utils/bytes.py helpers). For more ergonomic scripts, see contracts/examples/token/tests_local.py.

⸻

7) Deploy to Devnet/Testnet

7.1 Fund the deployer (devnet)

If you have a faucet configured in .env, run:

python -m contracts.tools.deploy \
  --pkg contracts/build/tutorial_token.pkg.json \
  --init-args '["TutorialToken","TT",18,"<OWNER_ADDR_HEX>"]' \
  --faucet

Or fund the deployer address manually and omit --faucet.

7.2 Deploy

python -m contracts.tools.deploy \
  --pkg contracts/build/tutorial_token.pkg.json \
  --init-args '["TutorialToken","TT",18,"<OWNER_ADDR_HEX>"]'

This prints:
	•	tx hash
	•	receipt (gas used / status)
	•	deployed address

Record the address as TOKEN_ADDR.

⸻

8) Interact (Transfers, Approvals, Permit)

Balance check

python -m contracts.tools.call \
  --addr $TOKEN_ADDR \
  --abi contracts/fixtures/abi/token20.json \
  --fn balanceOf \
  --args '["<OWNER_ADDR_HEX>"]'

Mint (owner-only)

python -m contracts.tools.call \
  --addr $TOKEN_ADDR \
  --abi contracts/fixtures/abi/token20.json \
  --fn mint \
  --args '["<OWNER_ADDR_HEX>", 1000000]'

Transfer

python -m contracts.tools.call \
  --addr $TOKEN_ADDR \
  --abi contracts/fixtures/abi/token20.json \
  --fn transfer \
  --args '["<RECIPIENT_ADDR_HEX>", 2500]'

Approve & transferFrom

python -m contracts.tools.call \
  --addr $TOKEN_ADDR \
  --abi contracts/fixtures/abi/token20.json \
  --fn approve \
  --args '["<SPENDER_ADDR_HEX>", 5000]'

python -m contracts.tools.call \
  --addr $TOKEN_ADDR \
  --abi contracts/fixtures/abi/token20.json \
  --fn transferFrom \
  --args '["<OWNER_ADDR_HEX>", "<RECIPIENT_ADDR_HEX>", 1000]'

Permit (off-chain signature)
	1.	Produce a PQ signature (via wallet/SDK) over the permit domain bytes,
	2.	Call:

python -m contracts.tools.call \
  --addr $TOKEN_ADDR \
  --abi contracts/fixtures/abi/token20.json \
  --fn permit \
  --args '["<OWNER>","<SPENDER>",5000,0,9999999999,"<SIG_HEX>"]'


⸻

9) Verify Source (Studio Services)

Prove your on-chain code hash matches the source+manifest:

python -m contracts.tools.verify \
  --addr $TOKEN_ADDR \
  --src contracts/examples/token/contract.py \
  --manifest contracts/examples/token/manifest.json

The service recompiles, checks the code hash, and stores a verification artifact. You can also query by tx hash.

⸻

10) Testing

10.1 Local unit tests

We ship a rich local suite under contracts/tests/. For token specifics:

pytest -q contracts/tests/test_token_stdlib.py

10.2 Example script

contracts/examples/token/deploy_and_test.py runs an end-to-end quickstart (adjust RPC & keys in .env).

⸻

11) Production Hardening
	•	Access control: Prefer roles for minters rather than sole ownership.
	•	Pausable: Gate state-changing functions behind a pause switch for incident response.
	•	Overflow/underflow: Use safe_uint and always check non-negativity for external amounts.
	•	Events: Emit all user-visible changes (Transfer, Approval, Mint, Burn) for indexers.
	•	Permit: Enforce nonce and deadline; reject expired or replayed permits.
	•	Upgrades: If using proxies, pin code hash (see contracts/docs/PATTERNS.md).
	•	Determinism: No time/sys/random I/O; keep pure & bounded inputs.
	•	Fees/Gas: Keep functions tight; avoid large loops over user input.

⸻

12) Troubleshooting
	•	Lint fails: remove non-stdlib imports; stick to allowed builtins (see contracts/CODESTYLE.md).
	•	OOG (out of gas): reduce per-call work or split operations.
	•	Deploy fails: check CHAIN_ID and PQ signature domain (wallet config).
	•	Permit invalid: confirm domain bytes and nonce/deadline; signature must be PQ scheme enabled in your wallet/SDK.
	•	Verification mismatch: ensure the exact contract.py and manifest.json are passed; no build-time variability.

⸻

13) Where to Go Next
	•	Add fee hooks or transfer restrictions,
	•	Integrate with AICF or DA for a tokenized data product,
	•	Build a governed minter with timelocks,
	•	Ship an explorer card by consuming your token events.

References
	•	contracts/docs/ARCHITECTURE.md
	•	contracts/docs/PATTERNS.md
	•	contracts/stdlib/token/*
	•	contracts/tools/*.py
	•	sdk/python quickstarts
