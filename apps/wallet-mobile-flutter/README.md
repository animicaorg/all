# Animica Wallet (mobile)

Flutter mobile wallet for the Animica chain. iOS + Android.

## What's working

- **Password-protected vault.** PBKDF2-SHA256 (200k rounds) → AES-GCM-128 layered on top of Keychain / EncryptedSharedPreferences. Unlock key lives only in RAM; lock-on-resume.
- **SPHINCS-SHAKE-128s** keypair generation + signing. Byte-for-byte parity with the chain's pure-Python fallback (mainnet runs `ANIMICA_ALLOW_PQ_PURE_FALLBACK=1`).
- **Dual-RPC failover.** Tries `mobile.animica.org/rpc` first, falls back to `rpc.animica.org/rpc`. Sticky last-good endpoint per session.
- **Live balance** via `state.getBalance`.
- **Receive** screen: QR + copy + share.
- **Send** screen: real `tx.sendRawTransaction` broadcast. Builds the canonical body, computes `build_sign_bytes` (matches `pq/py/sign.py`), CBOR-encodes the envelope, and submits. QR scan for the recipient address.
- **Buy ANM** deep-link to `buy.animica.org` with active address pre-filled.
- **Dapp browser** with full `window.animica` provider injection on whitelisted hosts (`animica.xyz`, `buy.animica.org`, `explorer.animica.org`, etc). Handles `animica_requestAccounts`, `animica_accounts`, `animica_chainId`, `animica_getBalance`, `animica_getNonce`, `animica_sendTransaction` (with native confirmation sheet). Sign rejection returns code 4001.
- **ANM-20 tokens.** User adds a contract address; wallet calls `balance_of()`, `name()`, `symbol()`, `decimals()` via `state.call`. Watchlist persists across launches.
- **NFT gallery** pulling `animica.xyz/api/marketplace/profile/<addr>/nfts`.
- **Wallets.json import/export** — same shape as the CLI's `~/.animica/wallets.json`.

## What's deferred

- **Per-account vardiff / hashrate dashboards.** Not in mobile-wallet scope; use `miner-wallet-flutter` or `pool.animica.org` for those.

## Signature schemes

Both schemes the chain accepts are implemented in pure Dart and verified
against the Python reference via golden-vector tests:

| Scheme | alg_id | pk bytes | sig bytes | Source |
|---|---|---|---|---|
| SPHINCS-SHAKE-128s | `0x1002` (4098) | 64 | 7856 | `services/keys.dart` |
| Dilithium3 / ML-DSA-65 | `0x1001` (4097) | 1952 | 3293 | `services/dilithium3.dart` |

Dilithium3 here is a Dart port of `python/animica/_vendor/dilithium_py/dilithium3.py` — the chain's reference impl, which is a commitment-style stub using only SHAKE-256 (the lattice ML-DSA-65 itself isn't deployed yet). 80 lines of Dart, no NTT, no rejection sampling. Round-trips to/from the Python reference byte-for-byte; see `test/dilithium3_test.dart`.

Choose a scheme when creating a new wallet in Settings → Create new wallet. SPHINCS is the default since `animica wallet create` produces SPHINCS keys.

## Build

```
cd apps/wallet-mobile-flutter
flutter pub get
flutter run                 # device or emulator
flutter build apk           # Android release
flutter build ios           # iOS release (requires Xcode)
```

Override RPC / chain at build time:

```
flutter run \
  --dart-define=ANIMICA_RPC_URL_PRIMARY=https://my.node/rpc \
  --dart-define=ANIMICA_RPC_URL_FALLBACK=https://my.fallback/rpc \
  --dart-define=ANIMICA_CHAIN_ID=1
```

## Architecture

```
lib/
├── main.dart           AuthGate — lock screen until unlocked
├── router.dart         go_router shell + push routes
├── theme.dart
├── constants.dart      network config + provider-host whitelist
│
├── models/
│   └── account.dart    Account dataclass + JSON round-trip
│
├── services/
│   ├── address.dart    Animica bech32m encode/decode
│   ├── keys.dart       SPHINCS-128s keygen + sign (Dilithium3 stub)
│   ├── vault.dart      Encrypted account storage
│   ├── auth.dart       Password setup + AES-GCM key derivation
│   ├── canonical.dart  Canonical CBOR + build_sign_bytes wrapper
│   ├── signer.dart     buildTransferBody/buildCallBody + signTx + broadcast
│   ├── rpc.dart        Multi-endpoint failover JSON-RPC client
│   ├── tokens.dart     ANM-20 watchlist + balance fetcher
│   └── import_export.dart  wallets.json interop
│
├── state/
│   ├── auth_state.dart    AuthStatus + vault provider
│   └── wallet_state.dart  Accounts, active address, balance providers
│
└── screens/
    ├── unlock.dart     Password setup / unlock
    ├── home.dart       Balance + address + Send/Receive/Buy buttons
    ├── send.dart       Real broadcast — QR scan, signed via vault key
    ├── receive.dart    QR + copy + share
    ├── buy.dart        url_launcher to buy.animica.org
    ├── browser.dart    flutter_inappwebview + window.animica injection
    ├── tokens.dart     Native ANM + ANM-20 watchlist
    ├── nfts.dart       Grid pulling marketplace profile API
    └── settings.dart   Accounts, password, import/export, wipe
```

## Wallets.json interop

Both directions work and produce a file the `animica` CLI reads as `~/.animica/wallets.json`:

```json
{
  "wallets": [
    {
      "label": "main",
      "address": "anim1…",
      "alg_id": 4098,
      "alg_name": "sphincs_shake_128s",
      "public_key_hex": "…",
      "secret_key_hex": "…",
      "pub_fingerprint": "…",
      "created_at": "2026-…",
      "pending_txs": []
    }
  ]
}
```

## Security model

- Secret keys never leave the device.
- The user's password is the encryption key for the local vault (PBKDF2 200k → AES-GCM-128).
- Lost password = lost keys. Export `wallets.json` and store offline before forgetting.
- The dapp browser only injects `window.animica` on hosts in `lib/constants.dart:walletProviderHosts`. Add new hosts there if you publish more Animica dapps.
- Every `animica_sendTransaction` request from a dapp triggers a native confirmation sheet showing from / to / amount / data preview before the signer runs.
