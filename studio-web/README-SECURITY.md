# Studio Web — Security Notes & Threat Model

Studio Web is a **client-side IDE** for Animica contracts. It compiles and simulates code **in the browser** (via `studio-wasm`) and connects to:
- a locally-installed **wallet extension** for key management and transaction signing;
- a node **JSON-RPC** endpoint for chain reads/writes;
- optional **Studio Services** (deploy/verify/faucet/artifacts proxy) which **never receives private keys**.

This document clarifies the trust boundaries, expected attacker capabilities, and the defenses we put in place. It complements the security docs across other packages (SDK, services, wallet extension).

---

## Security boundaries (who holds secrets?)

**Never server-side signing.**
- Private keys live **only** in the wallet extension (MV3) or your own signer; Studio Web never sees raw keys.
- Transactions are **built** in the page but **signed** via the extension’s approval flow (AIP-1193-like provider: `window.animica`).
- Optional Studio Services **relay** signed CBOR transactions and **verify** source→code hash; they do not sign.

**WASM/Pyodide execution is local.**
- Source → IR → simulate is performed in your browser using the trimmed, deterministic Python VM package (`studio-wasm`).
- No remote code execution is involved in simulation; there is **no server authority over simulation results**.

---

## Assets we protect

- **User intent & signing integrity**: ensure that what the user sees and approves is what gets signed (domain separation, chainId binding, CBOR determinism).
- **Session & origin integrity**: prevent other sites from silently initiating actions or reading Studio Web state.
- **Artifact integrity**: code / ABI / manifests are content-addressed and hashed before deploy & verify flows.
- **Network hardening**: avoid leaking secrets in URLs or logs; pin hosts and enforce CORS boundaries for services.

---

## Assumed attacker capabilities

- Malicious websites embedding Studio Web in iframes or opening it as a popup.
- XSS attempts via untrusted contract source, ABI, manifest, or on-chain event payloads.
- Malicious RPC endpoints trying to trick the client with inconsistent responses.
- Dependency supply-chain risks (NPM/py wheels).
- Phishing / UI spoofing to trick users into approving signatures.

**Non-goals**
- Protecting against local malware/Rootkits on the user machine (out of scope).
- Trusting a compromised wallet extension or OS-level exfiltration.

---

## Key controls & expectations

### 1) Provider & signing
- The in-page provider is injected by the wallet extension (`window.animica`). Studio Web **does not** inject its own signer.
- All sign requests include **domain separation** and **chainId** in the sign bytes (CBOR canonicalization). This prevents cross-chain replay and “invisible parameter change” attacks.
- UI shows the **exact CBOR/decoded fields** (to/from/amount/nonce/gas/chainId) prior to handing off to the wallet.

### 2) Content Security Policy (CSP)
- Recommend hosting with a strict CSP similar to:

default-src ‘self’;
script-src ‘self’ ‘wasm-unsafe-eval’;
worker-src ‘self’ blob:;
style-src ‘self’ ‘unsafe-inline’;
img-src ‘self’ data: blob:;
connect-src ‘self’ https://your-rpc.example https://your-services.example wss://your-rpc.example;
frame-ancestors ‘none’;
base-uri ‘none’;
form-action ‘self’;

- `frame-ancestors 'none'` blocks clickjacking; Studio Web **should not be framed**.

### 3) CORS / origins
- Studio Web only calls the RPC and services URLs configured in `.env` (`VITE_RPC_URL`, `VITE_SERVICES_URL`).
- Services enforce **strict CORS allowlists** and **token-bucket rate limits** (see `studio-services`).

### 4) XSS surface area
- Treat all user inputs and all **on-chain data** as untrusted: ABI names, event data, logs, addresses, and JSON.
- **Never** render untrusted HTML. Use text-only rendering and JSON stringification with escaping.
- Monaco editor content is isolated; do not enable arbitrary HTML renderers.
- Avoid `innerHTML`; use React DOM auto-escaping and safe components.

### 5) Supply chain hygiene
- Lock dependency versions; run `npm audit`/`pnpm audit` and SCA scans in CI.
- Prefer deterministic builds; review any packages that evaluate code at install time.
- WASM/Pyodide artifacts are pinned in `studio-wasm/pyodide.lock.json`.

### 6) Network & transport
- Require **HTTPS/WSS** in production to protect traffic and extension handshakes.
- Do not place API keys or secrets in query strings; use headers; avoid logging auth details.

### 7) Artifact integrity & verification
- Pre-deploy: compute code hash/digest (SDK/helpers) and include it in UX.
- Post-deploy: recommend “verify source” flow via Studio Services, which recompiles and **matches code hash** against on-chain address (no server-side signing involved).
- Artifacts are stored as **content-addressed** blobs on services; responses carry digests.

### 8) Permissions & storage
- Studio Web keeps minimal local state (editor buffers, UI prefs). No keys are stored.
- Wallet permissions are granted **per-origin** in the extension. The app requests connect/send permissions explicitly with user approval and can be revoked by the user.

### 9) DoS & robustness
- UI backs off and shows human-friendly errors on rate limits.
- WS subscriptions auto-reconnect with jitter and cap retry rates (SDK behavior).
- Large RBAC/ACL is handled server-side in services (if deployed), not in the web app.

---

## Secure configuration checklist (prod)

- [ ] Serve over HTTPS; set HSTS.
- [ ] Set CSP with `frame-ancestors 'none'`; disallow third-party script origins whenever possible.
- [ ] Configure **strict CORS** on Studio Services to the exact Studio Web origin(s).
- [ ] Use **WSS** for websockets if your RPC supports it.
- [ ] Point `VITE_RPC_URL`/`VITE_SERVICES_URL` at trusted hosts (pin via infra or firewall).
- [ ] Enable cache-busting and subresource integrity (SRI) for static assets if you host them behind a CDN.
- [ ] Run SAST/DAST and dependency audit in CI; pin Pyodide/WASM artifacts.
- [ ] Monitor logs for anomalies; ensure logs never contain private keys or sensitive headers.
- [ ] Keep wallet extension up to date; review its origin permission lists.

---

## Developer notes

- This app **does not** bake in any privileged secrets; all sensitive operations are user-authorized via the wallet.
- Simulation results are local and deterministic. They **do not guarantee** on-chain outcomes; network state can differ.
- Avoid adding “helper” endpoints that mutate state or sign on behalf of the user—this would break the threat model.

---

## Reporting vulnerabilities

If you discover a security issue, please **do not** open a public issue. Contact the maintainers privately with a proof-of-concept and environment details. We will triage, coordinate a fix, and credit reporters according to our disclosure policy.

