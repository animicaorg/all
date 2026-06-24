# animica-fastpow

Native SHA3-256 proof-of-work nonce scanner for the Animica CPU miner.

The Animica CPU miner's hot loop is otherwise pure Python, which caps multi-core
hashrate (the per-nonce Python overhead dominates). This package runs the loop in
C with the GIL released, so each miner worker gets native hashrate.

```python
import animica_fastpow as fp
assert fp.available()

# digest = SHA3-256(prefix || mix_seed || nonce.to_bytes(8, "little"))
# returns the first (nonce, digest) with int(digest, "big") <= target, else None
res = fp.scan(prefix, mix_seed, target_32_be, start_nonce, iterations)
```

The miner (`animica.animica_cpu_miner_repoexact`) imports this automatically and
falls back to its pure-Python loop if the extension isn't installed — so it's an
*optional* accelerator, never a hard requirement.

## Install

Most people don't install this package directly — it ships with the full
Animica client:

```bash
pip install animica          # the complete client; native CPU miner included by default
pip install "animica[all]"   # everything above PLUS every optional extra
```

- `pip install animica` — the complete client. Everything to mine, run a node,
  use the wallet, deploy Python contracts, run `animica up` (the unified miner:
  PoW + useful-work + GPU train/serve + Studio functions), and use the Studio
  SDK. The native CPU miner (`animica-fastpow`) is included BY DEFAULT. This is
  what most people want.
- `pip install "animica[all]"` — everything above PLUS every optional extra: Qt
  desktop-wallet QR codes, the full distributed Studio client (cloudpickle for
  closures + omni-sdk for on-chain ANM escrow), and all server/operator
  dependencies pinned. Use it if you want the kitchen sink or are running
  pool/API infrastructure.

Quote the extras form as `pip install "animica[all]"` (with quotes) so
zsh/macOS does not glob the brackets.

To install only this native accelerator:

```bash
pip install animica-fastpow         # prebuilt wheel (Linux/macOS/Windows)
pip install "animica[fast]"          # via the animica miner extra
```

From source (needs a C compiler — gcc/clang, or MSVC Build Tools on Windows):

```bash
pip install ./mining/native
```

## Correctness

The C SHA3-256 is the public-domain tiny_sha3 (NIST SHA3, 0x06 padding) and is
verified at build/test time to be byte-identical to Python's `hashlib.sha3_256`
and to the miner's `digest_from_sign_bytes`. A wrong hash would get every share
rejected, so parity is checked, not assumed.

## Performance

The self-contained keccak is portable (no OpenSSL dependency, so it ships as a
single `.pyd`/`.so` with no external DLLs). A follow-up AVX2 4-way batched keccak
is the path to a further ~4× per core.
