# Explorer Web — Architecture & Charting Guide

This document explains how **explorer-web** is organized, how data flows from node and services into the UI, and how the charting layer renders large, live-updating streams without jank.

> **Goals**
>
> - Fast first paint and predictable updates (live heads and recent txs)
> - Clear state ownership and derived selectors for memoized views
> - Streaming-friendly, backpressure-aware WebSocket handling
> - Lightweight charting primitives that work in all modern browsers

---

## High-Level Overview

The app is a React SPA built with Vite. It talks to:

1. **Node JSON-RPC** (HTTP) — point-in-time queries (blocks, tx, receipts)
2. **Node WebSocket** — subscriptions (`newHeads`, optionally `pendingTxs`)
3. **Studio Services** (optional) — verified source/artifacts, simulate/verify/faucet
4. **Explorer REST** (optional) — pre-aggregated indexes if the node exposes them

**Packages used here**:
- `src/services/*` — network clients (RPC, WS, services, explorer API)
- `src/state/*` — feature stores (network, blocks, txs, aicf, da, beacon, poies, peers…)
- `src/components/*` — presentation and widgets (tables, charts)
- `src/pages/*` — route containers that bind stores + components
- `src/utils/*` and `src/types/*` — helpers and typed models

---

## Data Flow

            ┌──────────────┐           ┌────────────────┐
            │   Browser    │           │  Studio Svcs   │
            │    (UI)      │           │   (optional)   │
            └──────┬───────┘           └───────┬────────┘
                   │                           │
                   │HTTP (fetch)               │HTTP (fetch)
                   │WS (tungstenite-compatible)│
    ┌──────────────▼─────────────┐             ▼
    │         services/          │      services/servicesApi.ts
    │   rpc.ts     ws.ts         │
    └──────────────┬─────────────┘
                   │
                   │ dispatch domain DTOs
                   ▼
           ┌───────────────┐       batched actions       ┌─────────────────┐
           │   state/*     ├────────────────────────────►│   selectors     │
           │ (feature zns) │◄───────── derived data ◄────└─────────────────┘
           └───────┬───────┘
                   │
                   ▼
             React components
          (tables, charts, pages)

- **services/rpc.ts** — idempotent fetch + retries, typed JSON-RPC
- **services/ws.ts** — auto-reconnect WebSocket + backoff, emits structured frames
- **workers/wsBuffer.worker.ts** — coalesces many WS frames into fewer UI events to avoid render thrash
- **state/** — colocated Zustand stores per feature domain with:
  - **raw** entity caches
  - **timeseries ring buffers** for charts
  - **selectors** for memoized slices

### Ownership and Boundaries

- The `network` store owns connectivity (RPC URL, WS status, head height).
- The `blocks` store owns paginated blocks and a small **recent window** for live charts.
- The `txs` store owns latest txs feed and search filters.
- The `poies` store owns PoIES rolling metrics (Γ, fairness, proof mix) computed via `utils/poies_math`.
- The `aicf`, `da`, `beacon`, `peers`, `contracts`, `address` stores own their domain data.
- Pages only select **derived slices**; they never mutate raw caches directly.

---

## Subscriptions & Backpressure

**Problem:** `newHeads` can burst (e.g., reorgs or high-freq testnets). Naively setState per frame causes jank.

**Solution:**
- `ws.ts` parses frames and posts them to `wsBuffer.worker.ts`.
- The worker merges/coalesces frames into **interval buckets** (e.g., every 250–500ms) and posts a compact update batch back to the UI thread.
- Stores apply batched updates inside a single Zustand `set` call (minimal renders).
- Components subscribe with shallow selectors to only re-render when relevant fields change.

**Reorg Handling:**
- `blocks` maintains a small **fork-aware window**: on head rollback, it trims inconsistent tips and refetches the canonical head–N..head range.
- Timeseries buffers are keyed by canonical height; on rollback, samples beyond the new head are dropped.

---

## Caching & Pagination

- **Blocks List:** cursor = `(fromHeight, pageSize)`; cache keyed by height ranges.
- **Tx List:** timestamp/height cursors and filter params; memoized pages.
- **Contracts & Artifacts:** cached by address/codeHash; optional Studio Services hydrate verified metadata.
- **Eviction Policy:** LRU by section with soft caps (e.g., keep last 200 blocks in memory), older pages re-fetched on demand.

---

## Error Handling

- **services/** normalizes transport errors into typed Error objects.
- `state/toasts.ts` surfaces human messages (rate-limits, CORS, connection lost).
- **Retry Policy:**
  - HTTP: exponential backoff with jitter; caps to a sane limit.
  - WS: backoff + jitter; fast-retry on first drop, slower on repeated failures.
- **Timeouts:** fetch with abort signals (node and browser).

---

## Type Model

- Core types live in `src/types/core.ts` (Head, Block, Tx, Receipt…) and domain files (AICF, DA, Beacon, PoIES).
- All services return **validated** objects; zod schemas in `utils/schema.ts` are used where free-form payloads exist (e.g., explorer REST).

---

## Charting Approach

The chart components are designed for **streaming data** and **large windows** with minimal overhead. Components:

- `TimeSeriesChart.tsx` — continuous line charts (TPS, block time, Γ)
- `StackedBarChart.tsx` — proof mix per block (% HashShare/AI/Quantum/…)
- `DonutChart.tsx` — fairness/market share (e.g., provider stakes)
- `GaugeChart.tsx` — utilization/health indicators
- `Sparkline.tsx` — small, memoized inline timeseries for tables

### Rendering Strategy

- **SVG for static/low-point counts**, Canvas for dense/animated plots:
  - TimeSeries/StackedBar use Canvas when points > threshold; otherwise SVG.
  - Donut/Gauge are SVG for text/ARIA accessibility.
- **requestAnimationFrame batching**:
  - Points appended into ring buffers in stores.
  - Paint is scheduled on the next rAF with **at-most-once per frame**.
- **Downsampling/Decimation**:
  - Largest-Triangle-Three-Buckets (LTTB) on the fly for windows with > N samples.
  - For stacked bars, aggregated buckets per pixel column to avoid overdraw.
- **Scales & Layout**:
  - Linear/log scales implemented with small helpers (no heavy chart lib).
  - Axis ticks computed from container size, with locale-aware formatting via `components/charts/chart.theme.ts`.
- **Colors & Theme**:
  - Colors sourced from CSS variables (`styles/theme.css`) so dark/light theme is automatic.
  - Proof-mix colors centralized in `components/poies/ProofMixLegend.tsx`.

### Accessibility

- Semantic roles and titles on SVG.
- Hidden textual summaries (min/avg/max) for screen readers.
- Focus rings and keyboard navigation where charts include interactive legends.

### Performance Notes

- **No React state inside chart primitives**; charts read data via stable props (memoized selectors).
- **No layout thrash**; measure container once with `ResizeObserver` and cache scales until size changes.
- **Stable keys**: ring buffers expose typed arrays to avoid churn.

---

## PoIES Metrics Pipeline

- **Inputs:** per-block proof weights ψ by type, acceptance caps S, target Θ.
- **Aggregation:** `utils/poies_math.ts` computes:
  - Γ (overall acceptance metric per block and EMA vs target)
  - Proof mix percentages (normalized ψ)
  - Fairness indices (Gini, HHI) over a rolling window
- **Selectors** in `state/poies.ts` expose:
  - Rolling Γ series
  - Mix stacked bars keyed by height
  - Fairness timeseries windows

---

## Pages & State Binding

- **Home**: head summary, TPS, avg block time, PoIES panels (Γ, fairness, mix)
- **Blocks**: paging + filters; detail includes DA/Proofs breakdown
- **Tx**: list + details (inputs/outputs/logs)
- **Address**: snapshot (balance/nonce) and activity
- **Contracts**: verified list + detail (ABI/events)
- **AICF**: providers/jobs/settlements, SLA charts
- **DA**: blobs and commitments
- **Beacon**: rounds timeline and latest beacon
- **Network**: peers, RTT, gossip health

Each page:
- Subscribes to minimal store slices via memoized selectors
- Triggers paginated fetches on intersection/visibility
- Renders charts using the timeseries buffers mentioned above

---

## Internationalization (i18n)

- Message catalogs in `src/i18n/*.json`.
- Number/date formatting centralized in `utils/format.ts` and `components/charts/chart.theme.ts`.
- Charts follow locale decimal/thousand separators.

---

## Testing

- **Unit tests** (Vitest):
  - services: mocked RPC/WS
  - math: PoIES metrics correctness
  - charts: render smoke tests with sample fixtures
- **E2E tests** (Playwright):
  - Live dashboard connects WS and updates Γ/fairness/mix in near-real time
- **Fixtures**:
  - Sample block/tx/AICF/PoIES JSON files for deterministic rendering

---

## Security & Privacy

- Read-only views; **no private keys** touch explorer-web.
- CORS restricted when running against production RPC/services.
- No PII is stored; local caches are ephemeral.

---

## Extensibility

- Add a new domain by:
  1. Creating `src/types/<domain>.ts`
  2. Adding `src/services/<domain>.ts` with typed clients
  3. Creating `src/state/<domain>.ts` (store + selectors)
  4. Building widgets/charts in `src/components/…`
  5. Wiring a route in `src/router.tsx`

- Charts accept **data adapters**; any series exposing `{ x: number; y: number }[]` (or stacked shapes) can render.

---

## Failure Modes & Guardrails

- **Missing WS**: app degrades to polling head (HTTP) with larger intervals.
- **Slow RPC**: retries/backoff; UI shows “stale” badge; charts freeze but stay interactive.
- **Reorg > window**: detail pages refetch canonical slices; visible banner indicates chain reorg.

---

## Appendix: Minimal Timeseries Buffer

- Fixed-size ring buffers store timestamps and values.
- Eviction is O(1); slices expose typed views (`Float64Array`) for fast Canvas draws.
- Selectors project buffers into screen-space using cached scales to avoid allocations.

---

*Last updated: keep in sync with services/rpc.ts, services/ws.ts, workers/wsBuffer.worker.ts, and components/charts/*.*
