# Studio Web — Architecture & Data Flow

This document explains how **Studio Web**’s front-end works end-to-end:
how the **Provider** (wallet/extension) interacts with the **TypeScript SDK**,
how the SDK talks to **chain nodes** and **Studio Services**, and how the
in-browser **WASM simulator** (Pyodide) is used for safe, deterministic
compile/simulate flows.

> **Security model (at a glance)**
>
> - All **private keys remain client-side**. Signing flows go through the
>   browser wallet provider (`window.animica`) or other injected providers.
> - Studio Services **never sign** transactions and never handle your private keys.
> - CORS and API-key controls protect Studio Services. Rate limits and token
>   buckets defend against abuse.
> - The simulator runs **locally** (WASM + Pyodide) with deterministic runtime,
>   no network access, and zero side effects on the chain.

---

## High-Level Components

```mermaid
flowchart LR
  subgraph Browser [Browser App (Studio Web)]
    UI[React UI] --> State[Zustand Store]
    State --> SDK[@animica/sdk (TS)]
    State --> WASM[studio-wasm (Pyodide Worker)]
    UI -. provider API .-> Provider[(window.animica)]
  end

  SDK -- JSON-RPC (HTTP/WS) --> Node[(Animica Node)]
  SDK -- HTTPS/JSON --> Services[(Studio Services API)]

  Services -. optional .-> DA[(Data Availability)]
  Services -. optional .-> Storage[(Artifacts Storage)]
  Services -. optional .-> Queue[(Verify/Jobs Queue)]

Browser App (Studio Web)
React + Zustand orchestrate editor, simulator, deploy, verify, and explorer views.

Provider (Wallet Extension)
Exposes window.animica for account/chain management and signing (never server-side).

TypeScript SDK (@animica/sdk)
A thin, typed client around JSON-RPC (HTTP + WebSocket), transaction builders, encoders,
ABI-based contracts client, PQ signers (feature-gated), and helpers (DA, AICF, randomness).

Studio Services
A FastAPI backend providing optional convenience endpoints (deploy relay, verification,
artifact storage, faucet, simulate via local VM). It never signs user data.

WASM Simulator (studio-wasm)
In-browser, deterministic VM implementation delivered as a web worker running Pyodide.
It compiles source to IR, estimates gas, simulates calls, and captures logs/events.

⸻

Primary Data Flows

1) Compile & Simulate (Local, Side-Effect Free)

sequenceDiagram
  participant UI as Editor UI
  participant WASM as studio-wasm (Worker)
  participant VM as Pyodide VM
  UI->>WASM: compileSource(manifest, source)
  WASM->>VM: load vm_pkg, link stdlib
  VM-->>WASM: {ir, diagnostics, gas_upper_bound}
  UI->>WASM: simulateCall(ir, func, args, seed)
  WASM->>VM: run_call(ir, func, args, ctx)
  VM-->>WASM: {returnData, logs, gas_used}
  WASM-->>UI: surface results + events

	•	Deterministic: Results depend only on inputs and VM context.
	•	Isolated: No network calls; perfect for preflight checks and teaching.

2) Deploy (User-Signed → Node)

sequenceDiagram
  participant UI
  participant SDK as @animica/sdk
  participant Provider as window.animica
  participant Node as Node RPC

  UI->>SDK: buildDeployTx(manifest, code, fees)
  SDK->>UI: SignBytes (tx bytes)
  UI->>Provider: sign(SignBytes)
  Provider-->>UI: signature
  UI->>SDK: sendSignedTx(signedTx)
  SDK->>Node: eth_sendRawTransaction
  Node-->>SDK: txHash
  SDK-->>UI: txHash
  SDK->>Node: (WS) subscribe newHeads / receipt
  Node-->>SDK: receipt (status, address)
  SDK-->>UI: receipt

	•	Gas Estimates: The SDK can call estimateGas*() helpers prior to signing.
	•	Receipts: The SDK provides polling or WS-based subscription to await inclusion.

3) Verify Source (Optional Server-Side Repro)

sequenceDiagram
  participant UI
  participant Svc as Studio Services
  participant VM as VM Compiler (services-side)
  participant Storage as Artifact Storage

  UI->>Svc: POST /verify {source, manifest, codeHash?}
  Svc->>VM: compile+hash(source, manifest)
  VM-->>Svc: {code_hash, diagnostics}
  Svc->>Storage: persist artifacts (content-addressed)
  Svc-->>UI: {status, result, pointers}

	•	Trust-minimized: Services recompute the code hash and publish metadata.
	•	Artifacts: Stored content-addressed; immutable and cacheable.

4) Read / Write Contract Calls

sequenceDiagram
  participant UI
  participant SDK
  participant Provider
  participant Node

  rect rgb(240,240,240)
  Note over UI,Node: Read Call (free)
  UI->>SDK: contract.foo.read(args)
  SDK->>Node: eth_call (no state change)
  Node-->>SDK: return data
  SDK-->>UI: decoded result
  end

  rect rgb(240,240,240)
  Note over UI,Node: Write Call (costs gas)
  UI->>SDK: contract.bar.write(args, fees)
  SDK->>UI: SignBytes
  UI->>Provider: sign(SignBytes)
  Provider-->>UI: signature
  UI->>SDK: sendSignedTx
  SDK->>Node: sendRawTransaction
  Node-->>SDK: txHash → receipt
  end

5) Events & Heads (Live Subscriptions)
	•	SDK connects via WebSocket to newHeads and logs filters.
	•	UI consumes Zustand selectors to update: status bar, explorer, and events panel.

⸻

Key Interfaces

Provider (window.animica)
	•	request({ method: "animica_accounts" | "animica_chainId" | "animica_sign", params })
	•	Emits accountsChanged, chainChanged
	•	May support PQ signers (Dilithium3, SPHINCS+) depending on wallet capabilities.

SDK Highlights (@animica/sdk)
	•	RPC: resilient HTTP/WS clients with retries and reconnect.
	•	Wallet: mnemonic/keystore utilities (browser uses provider; Node tests can use software keys).
	•	Tx: builders (buildDeploy, buildTransfer, buildCall), CBOR sign-bytes, gas helpers.
	•	Contracts: ABI-based client; dynamic & codegen paths.
	•	Extras: DA client, AICF client, randomness beacon tools, light client verify (headers/DA).

Studio Services (Optional)
	•	Deploy Relay: Accepts signed CBOR tx and relays to RPC (no signing).
	•	Verification: Recompiles artifacts and stores results & metadata.
	•	Artifacts: Write-once content-addressed storage (filesystem or S3).
	•	Faucet (dev/test): guarded by API-key & rate limits.
	•	Simulate: Can re-run local VM server-side for reproducibility.

⸻

State Management & React Integration
	•	Zustand stores split by domain (network, account, project, compile, simulate, deploy, verify, aicf, da, toasts).
	•	Hooks (useCompile, useSimulate, useDeploy, etc.) encapsulate orchestration and error handling, returning stable interfaces to components.

⸻

Error Handling & Telemetry
	•	SDK errors surface rich context (RPC method, params redaction, retry count).
	•	Services errors follow JSON problem+status format; mapped into UI toasts with actionable hints.
	•	Metrics (services): Prometheus /metrics; request IDs & structured logs (JSON).

⸻

Versioning & Compatibility
	•	SDK version is embedded in user agents for RPC requests and exposed via version.ts.
	•	Simulator (studio-wasm) pins Pyodide version and VM package checksums for reproducibility.
	•	Services publish /version and OpenAPI schema with minor enrichments.

⸻

Typical Dev Topologies

Local-Only (No Services)
	•	Studio Web ↔ Node RPC
	•	Simulator runs in-browser; verification skipped or done locally.

With Services
	•	Studio Web ↔ Node RPC
	•	Studio Web ↔ Studio Services (verify, artifacts, faucet)
	•	Services ↔ Storage (FS/S3), optional DA

⸻

Sequence: Scaffold → Simulate → Deploy → Verify

sequenceDiagram
  participant UI
  participant WASM
  participant SDK
  participant Provider
  participant Node
  participant Svc

  UI->>WASM: compileSource(manifest, source)
  WASM-->>UI: {ir, gas_estimate}
  UI->>WASM: simulateCall(ir, "init", [params])
  WASM-->>UI: ok
  UI->>SDK: buildDeployTx(manifest, code, fees)
  SDK-->>UI: signBytes
  UI->>Provider: sign(signBytes)
  Provider-->>UI: signature
  UI->>SDK: sendSignedTx(signedTx)
  SDK->>Node: sendRawTransaction
  Node-->>SDK: txHash → receipt (address)
  SDK-->>UI: receipt
  UI->>Svc: POST /verify {source, manifest}
  Svc-->>UI: {status: verified, code_hash}


⸻

Notes on Performance
	•	WASM boot: Pyodide is lazy-loaded and memoized; the worker survives across sessions when possible.
	•	Chunking: Large artifacts and sources are streamed when uploading to services.
	•	Retries: SDK RPC calls implement exponential backoff with jitter; WS auto-reconnects.

⸻

Extensibility
	•	Add new panels/pages by composing hooks and stores.
	•	New chain features: extend SDK types and RPC surface; UI changes remain minimal.
	•	New language targets: simulator currently expects Python contracts; IR path allows growth.

⸻

Threat Model (Summary)
	•	No server-side signing: Eliminates a class of server compromise risks.
	•	Strict CORS: Services only allow configured origins.
	•	API Keys: Gate sensitive routes (faucet); per-IP and per-key buckets.
	•	Content Addressing: Immutable artifact integrity by design.
	•	Light Client Verify: Optional, to validate headers/DA proofs in the browser or services.

⸻

Glossary
	•	ABI: Contract function/event schema for encoding/decoding calls/logs.
	•	IR: Intermediate representation used by the deterministic VM.
	•	CBOR SignBytes: Canonical transaction payload signed by the wallet.
	•	DA: Data Availability—pinning and proof of inclusion.
	•	AICF: AI/Quantum compute facility; enqueue and retrieve verified outputs.

⸻

Last updated: synchronized with the SDK/Services/WASM modules in this repository.
