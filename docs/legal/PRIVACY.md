<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Privacy & Telemetry Policy (Defaults: **Off**)

We care about user privacy and developer trust. All first-party binaries, SDKs, and the website ship with **telemetry disabled by default**. When you explicitly opt in, we collect **minimal, anonymous, aggregate** metrics to improve stability and performance. We never collect secrets.

> **Quick facts**
>
> - **Default:** Off for node, wallets, SDKs, explorer, studio, and website analytics.  
> - **Secrets:** We never collect private keys, mnemonics, seeds, raw messages, or clipboard contents.  
> - **Address data:** We do **not** collect wallet addresses or transaction payloads.  
> - **IP handling:** If a processor observes IPs, we truncate or discard them at ingestion where supported.  
> - **DNT/GPC:** We honor **Do Not Track** and **Global Privacy Control** in the website and services.  
> - **Self-hosting:** You can run all components without third-party analytics.

---

## 1) Scope

This policy covers first-party software and sites maintained by the Animica project:

- **Node & services:** `core/`, `rpc/`, `studio-services/`, `da/`, `p2p/`, etc.
- **Wallets:** Browser extension (MV3) and Flutter desktop/mobile apps.
- **Apps:** Explorer (desktop & web), Studio Web/WASM.
- **SDKs:** Python, TypeScript, Rust (no telemetry by default).
- **Website:** Static site with optional, **opt-in** analytics adapters.

It does **not** cover third-party forks, community deployments, or independent operators.

---

## 2) What we collect (only if you opt in)

When enabled, telemetry events are small, schema-versioned records. Typical fields:

- **Runtime info:** app/module name, version/commit, platform/OS, CPU arch.
- **Config flags (non-sensitive):** feature flags, selected network **chainId** (not addresses).
- **Performance:** cold/warm start times, API latencies, error code counts.
- **Crashes:** exception type & stack (symbol names only), **no memory dumps**.
- **Usage counters:** e.g., “transactions submitted: 1”, **not the contents**.
- **Timestamps:** UTC; coarse routing region where applicable (not precise location).

### Data we **never** collect
- Private keys, mnemonics, seeds, passphrases, keystore files, session pins.
- Complete transaction bodies, signatures, or contract call data.
- Exact wallet addresses or contact information.
- Clipboard contents, screenshots, or screen recordings.
- Precise geolocation.

---

## 3) How to opt in / out

Everything is **off by default**. You can enable/disable at build, env, config, UI, or CLI.

### Environment variables (examples)
| Component            | Disable (default)         | Enable (opt-in)              |
|---------------------|---------------------------|------------------------------|
| Node / services     | `ANIMICA_TELEMETRY=0`     | `ANIMICA_TELEMETRY=1`        |
| Wallet (extension)  | — (UI toggle default off) | UI: **Settings → Privacy**   |
| Wallet (Flutter)    | — (UI toggle default off) | UI: **Settings → Privacy**   |
| Website analytics   | *(unset)*                  | `PUBLIC_ANALYTICS=plausible` or `posthog` |

> If both env and CLI are set, the **CLI flag wins**.

### CLI flags (where applicable)

–telemetry=off|on         # default: off
–crash-reports=off|on     # default: off

### Config file (YAML/TOML snippets)
```toml
[telemetry]
enabled = false  # default
crash_reports = false

Browser/site behavior
	•	Honors navigator.doNotTrack and GPC; analytics won’t load if either is active.
	•	Uses cookieless mode where supported (e.g., Plausible). No fingerprinting.

⸻

4) Website analytics (optional, opt-in)

If explicitly enabled by deployers:
	•	Preferred: Plausible (cookieless, IP anonymization).
	•	Alternative: PostHog (self-host recommended); session recording disabled; IPs de-identified.
	•	DNT/GPC: Always respected; analytics not loaded when present.
	•	Metrics: page views, route names, referrers, coarse device/OS, outbound link clicks.

⸻

5) Logs & metrics
	•	Application logs: By default stay local (stdout/files). You control where they go.
	•	Prometheus metrics: Exposed locally by default; do not expose publicly without auth.
	•	RPC access logs: Can be disabled or redacted; they include method names, durations, codes—not request bodies.

⸻

6) Data processing & retention
	•	Legal bases: Legitimate interests (stability, security) and/or explicit consent (opt-in toggles).
	•	Processors (optional): Plausible/PostHog (analytics), Sentry-compatible backends (crash).
Prefer self-hosted deployments.
	•	Retention:
	•	Raw telemetry: ≤ 30 days
	•	Aggregates/metrics: ≤ 180 days
	•	Crash minima: ≤ 90 days
Operators may choose stricter limits.
	•	Security: TLS in transit; encryption at rest where supported; least-privilege access.

⸻

7) Your controls & rights
	•	Turn it off: Use the env/CLI/UI toggles above.
	•	DNT/GPC: We honor both for web properties.
	•	Exports/Deletion: If you opted in and want deletion of any associated records, contact us; provide timestamps and approximate client info. We strive to comply where feasible given our anonymous design.
	•	Jurisdictions: Where applicable (e.g., GDPR/CCPA), contact us to exercise rights; our minimal, anonymous approach means we typically cannot identify individuals.

Contact: privacy@animica.org

⸻

8) Children’s privacy

Our software and websites are not directed to children under the age of 13 (or the equivalent minimum age in relevant jurisdictions). We do not knowingly collect personal data from children.

⸻

9) Self-hosting & third-party services

You can deploy without any third-party analytics, crash collection, or CDNs. If you enable third-party processors, review their policies and data locations. Prefer EU/EEA or regional hosting that matches your requirements.

⸻

10) Changes to this policy

We may update this policy as features evolve. Material changes that affect telemetry defaults or scope will be documented in the project CHANGELOG and, where appropriate, surfaced in-product.
	•	Version: 1.0
	•	Effective date: 2025-10-11
	•	History: Initial publication (defaults off, DNT/GPC honored, anonymous metrics only when enabled)

⸻

11) Attributions
	•	Inspired by privacy-first telemetry patterns in modern OSS (cookieless analytics, minimized schemas).
	•	The text of this policy is available under CC BY-SA 4.0.

