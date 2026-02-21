# Studio ENA Guided Automation

Animica Studio now includes an ENA guided automation layer that turns ENA participation into wizard-first flows.

## Profiles and defaults

- Default profile: `https://mainnet.animica.org/rpc`
- Local profile fallback: `http://127.0.0.1:8545/rpc`
- Default contribution plan: `ena_dataset_build` with safe budget `100`
- Default training preset: `quick` (5 minute CPU LoRA/tiny)

## Flows

### 1) Contribute (CPU)

Button: **Contribute (CPU)**

Guided steps:
1. Select type (`dataset` or `eval`)
2. Suggested intensity
3. Local run with logs
4. Manifest + SHA256 receipt
5. Verification
6. AICF submit
7. Success receipt (job id, artifact hash, credits)

CLI equivalent:
```bash
animica ena contribute --type dataset|eval --auto --budget 100
```

### 2) Watch & Fetch Checkpoints

Button: **Watch & Fetch Checkpoints**

Guided steps:
1. Discover checkpoints
2. Download and verify hash
3. Index in local store (origin + metadata)

CLI equivalents:
```bash
animica ena checkpoints list
animica ena checkpoints fetch --latest
```

### 3) Train Locally (CPU)

Button: **Train Locally (CPU)**

Guided steps:
1. Pick base checkpoint
2. Pick dataset source
3. Budget preset quick/medium/long
4. Stream progress + metrics
5. Save checkpoint + receipt

CLI equivalent:
```bash
animica ena train --preset quick|medium|long
```

### 4) Publish to Network (DA)

Button: **Publish to Network (DA)**

Guided steps:
1. Select local checkpoint
2. Validate manifest/hash
3. Push to DA (or dev stub)
4. Register in AICF
5. Show version/commitment

CLI equivalent:
```bash
animica ena publish --checkpoint <id>
```

### 5) Use ENA (Inference)

Button: **Use ENA (Inference)**

Guided steps:
1. Toggle local vs network
2. Show fee estimate + AICF split
3. Run request and display latency/tokens/credits
4. Save redaction-ready local history

CLI equivalent:
```bash
animica ena infer --local|--network --prompt "..."
```

## Auto mode

Button: **Auto mode** runs contribute → watch/fetch checkpoint → set active checkpoint.

Headless command export example:
```bash
animica ena contribute --type dataset --auto --budget 100 && animica ena checkpoints fetch --latest
```

## Debug bundles

Each flow run can generate a JSON debug bundle containing:
- `rpc.discover` output/capabilities
- step logs
- manifests + hashes
- history entries

## Screenshots

- TODO: `![ENA dashboard](./images/studio-ena-dashboard.png)`
- TODO: `![Contribute flow](./images/studio-ena-contribute.png)`
