# Animica Wallet (mobile)

Flutter mobile wallet for the Animica chain. iOS + Android.

## What's in v0.1

- Password-protected vault (PBKDF2 + AES-GCM over Keychain / EncryptedSharedPreferences)
- SPHINCS-SHAKE-128s keypair generation + signing (matches chain's pure-Python fallback)
- Live balance via `state.getBalance` against `rpc.animica.org`
- Receive screen with QR + share
- Buy ANM deep-link to `buy.animica.org` with the active address pre-filled
- Built-in dapp browser (whitelisted Animica hosts, bookmarks)
- NFT gallery pulling from `animica.xyz` marketplace API
- Wallets.json import / export interop with the `animica` CLI
- Tokens tab scaffolded (native ANM works; ANM-20 list lands in v0.2)
- Send screen scaffolded (encoder lands in v0.2 — use CLI for now)

## What's missing (v0.2)

- Real Dilithium3 signing (needs ML-DSA-65 Dart port or WASM build)
- Canonical sign-bytes encoder for `tx.sendRawTransaction`
- ANM-20 balance discovery + transfers
- `window.animica` provider injection in the dapp browser
- Push notifications on inbound txs

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
flutter run --dart-define=ANIMICA_RPC_URL=https://my.node/rpc \
            --dart-define=ANIMICA_CHAIN_ID=1
```

## Wallet interop

The vault encodes accounts in a format compatible with `~/.animica/wallets.json`:

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

So you can export from the CLI and import into mobile, or vice-versa.

## Security

- Secret keys never leave the device.
- The user's password is the encryption key for the local vault (PBKDF2 200k rounds, AES-GCM-128).
- Lost password = lost keys. Export `wallets.json` and store it somewhere offline before forgetting.
- The dapp browser only injects `window.animica` on hosts listed in `lib/constants.dart:walletProviderHosts`.
