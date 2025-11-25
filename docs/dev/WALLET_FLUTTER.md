# Wallet (Flutter) — Building & Integrating (Mobile + Desktop)

A production-grade Flutter wallet for **Animica** targeting **iOS, Android,
macOS, Windows, and Linux**. This guide covers builds, signing, and how the app
integrates with Animica’s stack: **bech32m addresses**, **post-quantum (PQ)
signatures**, **CBOR transactions**, and the **minimal JSON-RPC**.

> This document is platform-agnostic. OS-specific packaging/signing steps are
> documented under `installers/wallet/*` and referenced here.

---

## TL;DR (one-liners)

> Ensure you have Flutter 3.x+, a recent Dart SDK, and platform toolchains installed.

```bash
# Android (debug)
flutter build apk --flavor dev --dart-define=PUBLIC_RPC_URL=https://rpc.dev.animica.xyz --dart-define=PUBLIC_CHAIN_ID=2

# Android (release)
flutter build appbundle --flavor prod --release

# iOS (debug to simulator)
flutter build ios --simulator --flavor dev

# iOS (release, archive in Xcode for signing)
flutter build ipa --flavor prod --export-options-plist ios/ExportOptions.plist

# macOS
flutter build macos --flavor prod

# Windows
flutter build windows --flavor prod

# Linux
flutter build linux --flavor prod

Runtime configuration is supplied via --dart-define or an env loader. Required:
	•	PUBLIC_RPC_URL – default node HTTP endpoint
	•	PUBLIC_WS_URL – (optional) node WebSocket endpoint
	•	PUBLIC_CHAIN_ID – numeric chain id (e.g., 1 mainnet, 2 testnet)

⸻

Architecture

┌─────────────────────────────────────────────────────────────────────┐
│                               UI (Flutter)                          │
│  • Screens: Home, Send, Receive, Activity, Settings                 │
│  • Components: AddressCard, TxList, NetworkPill                     │
├─────────────────────────────────────────────────────────────────────┤
│                        Wallet Core (Dart)                           │
│  • Keyring: mnemonic ↔ seed ↔ PQ keypairs, lock/unlock              │
│  • Addressing: bech32m HRP=anim, payload=(alg_id||sha3_256(pub))    │
│  • Tx builder: transfer/call/deploy → canonical CBOR SignBytes      │
│  • RPC/WS client: minimal JSON-RPC + subscriptions (newHeads)       │
│  • Storage: encrypted vault + app settings                          │
├─────────────────────────────────────────────────────────────────────┤
│             Native Bridges (optional for performance)               │
│  • PQ signers (Dilithium3 / SPHINCS+ SHAKE-128s) via FFI            │
│  • SHA3/Keccak fast paths (platform crypto/accelerated)             │
└─────────────────────────────────────────────────────────────────────┘

Design goals:
	•	Deterministic & safe: domain-separated signing, chain-bound transactions.
	•	Portable: pure-Dart fallbacks; native fast paths behind feature flags.
	•	No server-side signing: keys live only on user devices.

⸻

Dependencies
	•	Flutter 3.x+, Dart 3.x
	•	Platform SDKs:
	•	Android SDK + NDK (optional if using native PQ)
	•	Xcode 14+ for iOS/macOS
	•	Visual Studio (Desktop development with C++) for Windows
	•	GCC/Clang + GTK libs for Linux
	•	Crypto/PQ:
	•	Pure-Dart implementations for SHA3/Keccak and CBOR.
	•	Optional native plugins for PQ signatures (recommended in release).
	•	Storage:
	•	Secure storage per platform (Keychain/Keystore/DPAPI/libsecret)
	•	Encrypted on-disk vault for export/import.

⸻

Configuration

Add these to flutter run/flutter build via --dart-define or a .env adapter:
	•	PUBLIC_RPC_URL
	•	PUBLIC_WS_URL (optional)
	•	PUBLIC_CHAIN_ID
	•	WALLET_LOG_LEVEL (optional: debug|info|warn|error)

Example dev defines file (optional):

# .env.dev
PUBLIC_RPC_URL=https://rpc.dev.animica.xyz
PUBLIC_WS_URL=wss://rpc.dev.animica.xyz/ws
PUBLIC_CHAIN_ID=2
WALLET_LOG_LEVEL=debug


⸻

Key Management
	•	Mnemonic: BIP-39-like phrase → seed via PBKDF2/HKDF-SHA3-256 (not SHA2).
	•	Keys: per-account PQ keypair:
	•	Default Dilithium3 (fast, compact verification)
	•	Optional SPHINCS+ SHAKE-128s (stateless alternative)
	•	Addresses: payload = alg_id || sha3_256(pubkey) → bech32m("anim", payload).
	•	Vault: AES-GCM encrypted (device key + user PIN/biometrics). No background backups by default.

The alg_id is displayed to the user in account details and is embedded in signatures.

⸻

Transactions

Builder converts app intents into canonical CBOR and SignBytes:

class SendTx {
  final String to;              // bech32m (hrp anim…)
  final BigInt amount;          // lowest unit
  final BigInt gasPrice;
  final BigInt gasLimit;
  final String? dataHex;        // optional call/deploy payload (0x…)
  final int chainId;            // must match PUBLIC_CHAIN_ID
  final int? nonce;             // if null, fetched via RPC
  final String? memo;
  // ...
}

Signing:
	•	Domain: SignBytes = encodeCanonical({ chainId, nonce, gas, to, amount, data, memo })
	•	PQ signature with algId bound into the signature envelope.
	•	The app always verifies chainId and refuses to sign mismatched requests.

Submission:
	•	RPC call: tx.sendRawTransaction (CBOR blob)
	•	Returns: txHash (hex). Receipt watch via tx.getTransactionReceipt / WS.

⸻

Networking (RPC/WS)

A minimal client is sufficient. Example (Dart):

import 'dart:convert';
import 'package:http/http.dart' as http;

class JsonRpc {
  final Uri endpoint;
  int _id = 0;
  JsonRpc(String url) : endpoint = Uri.parse(url);

  Future<T> call<T>(String method, [Object? params]) async {
    final body = jsonEncode({
      'jsonrpc':'2.0',
      'id': ++_id,
      'method': method,
      'params': params ?? [],
    });
    final res = await http
        .post(endpoint, headers: {'content-type':'application/json'}, body: body)
        .timeout(const Duration(seconds: 20));
    final Map<String,dynamic> j = jsonDecode(res.body);
    if (j['error'] != null) {
      throw RpcError(j['error']['code'], j['error']['message'], j['error']['data']);
    }
    return j['result'] as T;
  }
}

class RpcError implements Exception {
  final int code; final String message; final Object? data;
  RpcError(this.code, this.message, [this.data]);
  @override String toString() => 'RpcError($code): $message';
}

WS subscriptions (newHeads) should auto-reconnect and de-dupe notifications.

⸻

PQ Signers (strategy)
	•	Default: pure-Dart wrappers (educational; slower) for dev builds.
	•	Production: platform plugins providing Dilithium3/SPHINCS+ via native code:
	•	Android: JNI + NDK static lib
	•	iOS/macOS: Swift/Obj-C bridging to C implementation
	•	Windows: C++/WinRT DLL
	•	Linux: .so via FFI
	•	Feature flags:
	•	--dart-define=PQ_NATIVE=1 enables native signer loading
	•	Fallback gracefully if unavailable (never crash on load).

All signers must implement a streaming SHA3-256; for CBOR hashing, use canonical map ordering.

⸻

UI/UX Notes
	•	Account switcher in top bar; show algId chip (e.g., Dilithium3).
	•	Copy address with bech32m checksum tinting; QR code support.
	•	Send flow:
	•	amount → address → review (gas/tip/chain) → confirm (biometrics/PIN) → submitting → done.
	•	Activity: combine pending (mempool) + confirmed (RPC receipts).
	•	Security:
	•	Optional screen-capture blocking (Android FLAG_SECURE)
	•	Clipboard warnings for large private material
	•	Export requires re-auth + explicit risk copy

⸻

Testing
	•	Unit tests:
	•	Key derivation, address codec, CBOR encode, domain-separated hash
	•	RPC stubs: head/nonce/balance
	•	Widget tests:
	•	Send flow (validations, review screen, confirmation)
	•	Integration (devnet):
	•	Use docs/dev/QUICKSTART.md to boot a dev node
	•	Fund test accounts; send transfer; await receipt

⸻

Platform-specific Build & Signing

Android
	•	App bundle (.aab) for Play:

flutter build appbundle --flavor prod --release


	•	Keystore: set storeFile, storePassword, keyAlias, keyPassword.
	•	NDK (if native PQ): define ABIs in android/app/build.gradle.

iOS
	•	Open ios/Runner.xcworkspace → set signing team, capabilities.
	•	For release:

flutter build ipa --flavor prod --export-options-plist ios/ExportOptions.plist


	•	Entitlements: Hardened runtime is off on iOS; ensure network permissions.

macOS
	•	Codesign + Notarize via installers/wallet/macos/sign_and_notarize.sh.
	•	Hardened runtime: enabled; JIT off.

Windows
	•	Build MSIX/NSIS via installers:

flutter build windows --flavor prod

Then package with installers/wallet/windows/msix/MakeMSIX.ps1.

Linux
	•	Build:

flutter build linux --flavor prod


	•	Package as AppImage / Flatpak / DEB/RPM via installers recipes.

See installers/wallet/* for full pipelines, version bump scripts, and CI.

⸻

Telemetry & Privacy
	•	Off by default. If enabled, only anonymous, aggregated metrics (e.g., app
version, platform, feature toggles). No addresses or hashes are sent.
	•	Gate with a clear opt-in toggle in Settings.

⸻

Error Model (Surface to UI)

Map RPC & wallet errors to human messages:

Code	Category	UX treatment
4001	UserRejected	Non-destructive; show “Cancelled” toast
4100	Unauthorized	Prompt to reconnect wallet
4200	UnsupportedMethod	Disable feature / show “coming soon”
4900	Disconnected	Retry with backoff; surface offline banner
5000	Internal	Log + crash-safe toast; suggest app restart


⸻

Example: Send Flow (Dart)

Future<String> sendTransfer({
  required String fromAddr,
  required String toAddr,
  required BigInt amount,
}) async {
  // 1) Fetch account state
  final nonce = await rpc.call<int>('state.getNonce', [fromAddr]);
  final gasPrice = BigInt.from(1200);
  final gasLimit = BigInt.from(120000);

  // 2) Build canonical sign bytes
  final signBytes = buildSignBytesCbor(
    chainId: chainId,
    nonce: nonce,
    to: toAddr,
    amount: amount,
    gasPrice: gasPrice,
    gasLimit: gasLimit,
  );

  // 3) PQ sign
  final sig = await keyring.sign(address: fromAddr, signBytes: signBytes);

  // 4) Wrap into raw CBOR tx (envelope includes pubkey/alg_id/signature)
  final rawTx = encodeRawTx(signBytes, sig); // Uint8List

  // 5) Submit
  final txHash = await rpc.call<String>('tx.sendRawTransaction', [bytesToHex(rawTx)]);
  return txHash;
}


⸻

CI/CD
	•	Build & Tests on PR:
	•	Unit/widget tests
	•	Lints & format
	•	Release (tagged):
	•	Build platform artifacts
	•	Codesign/Notarize (macOS/iOS), Codesign (Windows), package (Linux)
	•	Publish installers → update appcasts (Sparkle) / WinGet manifest
	•	See:
	•	installers/ci/github/wallet-*.yml
	•	installers/updates/*

⸻

Security Checklist
	•	Domain-separated signing for all messages/tx
	•	ChainId pinned into SignBytes
	•	Encrypted vault with device binding
	•	Biometric/PIN gating on sign/export
	•	Clipboard/screenshot guard toggles
	•	Strict network allowlist (optional)
	•	Crash logs scrubbed of PII

⸻

References
	•	Addressing: docs/spec/ADDRESSES.md
	•	Tx format & CBOR: docs/spec/TX_FORMAT.md, spec/tx_format.cddl
	•	RPC surface: spec/openrpc.json
	•	Installers & signing: installers/wallet/*
	•	Website downloads: website/src/pages/downloads.astro

