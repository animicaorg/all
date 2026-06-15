"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

// The app is served under basePath "/app", but client-side fetch() does not get
// the basePath prepended automatically (only <Link>/router do). So we hit the
// proxy routes with the explicit /app prefix, matching the rest of the app.
const API = "/app/api/pools";

// ---- Coordinator response shapes (loose; the coordinator may add fields) ----

interface PoolSummary {
  pool_id: string;
  name?: string;
  status?: string;
  base_model?: string;
  method?: string;
  round?: number;
  num_shards?: number;
  funded?: boolean;
  budget_anm?: string | number;
  budget_nano?: string | number;
  [key: string]: unknown;
}

interface PoolStatus {
  pool_id: string;
  name?: string;
  status?: string;
  base_model?: string;
  method?: string;
  round?: number;
  num_shards?: number;
  shards?: Record<string, number>;
  contributions?: Record<string, number>;
  shards_submitted?: number;
  funded?: boolean;
  budget_nano?: string | number;
  budget_anm?: string | number;
  paid_out_nano?: string | number;
  served_checkpoint?: {
    round?: number;
    checkpoint_hash?: string;
    [key: string]: unknown;
  } | null;
  reward_split?: Record<string, number>;
  [key: string]: unknown;
}

interface FundQuote {
  pool_id: string;
  pool_hash: string;
  treasury_address: string;
  chain_id: number | string;
  required_nano: string | number;
  required_anm: string | number;
  memo: string;
  round: number;
}

// ---- Animica wallet provider (window.animica), per ena-site/ena.js -----------

interface AnimicaProvider {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
}

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

/** The extension injects window.animica asynchronously and fires
 *  'animica#initialized' when ready — wait for it rather than assuming. */
function hasProvider(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.animica &&
    typeof window.animica.request === "function"
  );
}

function getProvider(timeoutMs = 1800): Promise<AnimicaProvider | null> {
  return new Promise((resolve) => {
    if (typeof window === "undefined") return resolve(null);
    if (hasProvider()) return resolve(window.animica as AnimicaProvider);
    let done = false;
    const settle = () => {
      if (done) return;
      done = true;
      resolve(hasProvider() ? (window.animica as AnimicaProvider) : null);
    };
    window.addEventListener("animica#initialized", settle, { once: true });
    window.addEventListener("ethereum#initialized", settle, { once: true });
    setTimeout(settle, timeoutMs);
  });
}

// ---- Helpers ----------------------------------------------------------------

function fmtMap(m?: Record<string, number>): string {
  if (!m || typeof m !== "object") return "";
  const ks = Object.keys(m);
  if (!ks.length) return "";
  return ks.map((k) => `${k}: ${m[k]}`).join(", ");
}

function budgetLabel(p: PoolSummary | PoolStatus): string {
  if (p.budget_anm != null) return `${p.budget_anm} ANM`;
  if (p.budget_nano != null) return `${p.budget_nano} nano`;
  return "—";
}

function statusTone(status?: string): string {
  switch (status) {
    case "training":
    case "active":
    case "running":
      return "text-emerald-400";
    case "funded":
      return "text-blue-400";
    case "complete":
    case "completed":
      return "text-white/50";
    case "paused":
      return "text-amber-400";
    default:
      return "text-white/60";
  }
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// ---- Page -------------------------------------------------------------------

export default function TrainingPoolsPage() {
  const [pools, setPools] = useState<PoolSummary[]>([]);
  const [poolsError, setPoolsError] = useState<string | null>(null);
  const [loadingPools, setLoadingPools] = useState(true);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [status, setStatus] = useState<PoolStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);

  // wallet
  const [hasWallet, setHasWallet] = useState<boolean | null>(null);
  const [account, setAccount] = useState<string | null>(null);

  // funding
  const [amount, setAmount] = useState("");
  const [fundMsg, setFundMsg] = useState<string | null>(null);
  const [fundErr, setFundErr] = useState<string | null>(null);
  const [funding, setFunding] = useState(false);

  // --- load pool list ---
  const loadPools = useCallback(async () => {
    setLoadingPools(true);
    setPoolsError(null);
    try {
      const res = await fetch(API, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || "could not load pools");
      const list: PoolSummary[] = data?.pools ?? [];
      setPools(list);
      // auto-select the first pool if none chosen yet
      setSelectedId((cur) => cur ?? list[0]?.pool_id ?? null);
    } catch (e) {
      setPoolsError(
        e instanceof Error ? e.message : "could not reach the coordinator",
      );
      setPools([]);
    } finally {
      setLoadingPools(false);
    }
  }, []);

  useEffect(() => {
    loadPools();
  }, [loadPools]);

  // detect wallet (non-blocking)
  useEffect(() => {
    let alive = true;
    getProvider(1800).then((prov) => {
      if (alive) setHasWallet(!!prov);
    });
    return () => {
      alive = false;
    };
  }, []);

  // --- load status for the selected pool, and keep it fresh ---
  const loadStatus = useCallback(async (poolId: string) => {
    setLoadingStatus(true);
    setStatusError(null);
    try {
      const res = await fetch(`${API}/${encodeURIComponent(poolId)}`, {
        cache: "no-store",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || "could not load status");
      setStatus(data as PoolStatus);
    } catch (e) {
      setStatusError(
        e instanceof Error ? e.message : "could not load pool status",
      );
      setStatus(null);
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setStatus(null);
      return;
    }
    loadStatus(selectedId);
    const iv = setInterval(() => loadStatus(selectedId), 15000);
    return () => clearInterval(iv);
  }, [selectedId, loadStatus]);

  // --- wallet connect ---
  async function connectWallet() {
    setFundErr(null);
    const prov = await getProvider(1800);
    if (!prov) {
      setHasWallet(false);
      setFundErr("No Animica wallet detected.");
      return;
    }
    setHasWallet(true);
    try {
      const accts = (await prov.request({
        method: "animica_requestAccounts",
      })) as string[] | undefined;
      const a = (accts && accts[0]) || null;
      if (a) setAccount(a);
      else setFundErr("No account returned by the wallet.");
    } catch (e) {
      setFundErr(
        e instanceof Error ? `Connection ${e.message}` : "Connection rejected.",
      );
    }
  }

  // --- poll confirm until funded ---
  async function pollConfirm(poolId: string, txid: string, requester: string) {
    for (let tries = 0; tries < 30; tries++) {
      const res = await fetch(
        `${API}/${encodeURIComponent(poolId)}/fund-confirm`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ txid, requester }),
        },
      );
      const j = await res.json().catch(() => ({}));
      if (res.ok && j?.funded) {
        setFundMsg("Funded ✓ — the pool is now bankrolled.");
        loadStatus(poolId);
        return;
      }
      const tone = j?.tx_status || "pending";
      setFundMsg(`Payment seen, awaiting confirmation… (${tone})`);
      await sleep(5000);
    }
    setFundErr("Could not confirm funding in time. Check back shortly.");
  }

  // --- fund flow: quote -> sendTransaction -> poll confirm ---
  async function fund() {
    setFundErr(null);
    setFundMsg(null);
    if (!selectedId) {
      setFundErr("Pick a pool first.");
      return;
    }
    const reward = parseFloat(amount);
    if (!(reward > 0)) {
      setFundErr("Enter a reward amount greater than 0.");
      return;
    }
    let acct = account;
    if (!acct) {
      await connectWallet();
      acct = account;
    }
    // connectWallet sets state async; re-read provider for the address if needed
    if (!acct) {
      const prov = await getProvider(800);
      if (prov) {
        try {
          const accts = (await prov.request({
            method: "animica_requestAccounts",
          })) as string[] | undefined;
          acct = (accts && accts[0]) || null;
          if (acct) setAccount(acct);
        } catch {
          /* user rejected */
        }
      }
    }
    if (!acct) {
      setFundErr("Connect an Animica wallet to fund a pool.");
      return;
    }

    setFunding(true);
    try {
      setFundMsg("Requesting a quote…");
      const qRes = await fetch(
        `${API}/${encodeURIComponent(selectedId)}/fund-quote`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ rewardAnm: reward, requester: acct }),
        },
      );
      const q = (await qRes.json()) as FundQuote & { error?: string };
      if (!qRes.ok) {
        throw new Error(q?.error || "quote failed");
      }

      setFundMsg("Approve the payment in your wallet…");
      const prov = await getProvider(1800);
      if (!prov) {
        setHasWallet(false);
        throw new Error("no wallet");
      }
      const txid = (await prov.request({
        method: "animica_sendTransaction",
        params: [
          {
            from: acct,
            to: q.treasury_address,
            value: String(q.required_nano),
            memo: q.memo,
          },
        ],
      })) as string;

      setFundMsg(`Payment sent: ${String(txid).slice(0, 18)}… — verifying…`);
      await pollConfirm(selectedId, String(txid), acct);
    } catch (e) {
      setFundErr(e instanceof Error ? e.message : "funding failed");
      setFundMsg(null);
    } finally {
      setFunding(false);
    }
  }

  const selected = pools.find((p) => p.pool_id === selectedId) || null;

  return (
    <div className="space-y-10">
      {/* header */}
      <section className="space-y-3">
        <p className="text-sm font-medium uppercase tracking-wide text-blue-400">
          ENA training pools
        </p>
        <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
          Fund and monitor training pools.
        </h1>
        <p className="max-w-2xl text-white/70">
          Each pool trains the open{" "}
          <Link href="/about-ena" className="text-blue-400 hover:text-blue-300">
            ENA model
          </Link>{" "}
          a round at a time. Bankroll a pool with ANM and the network trains it
          for you; have a rig?{" "}
          <Link
            href="/mining-onboard"
            className="text-blue-400 hover:text-blue-300"
          >
            Point it at a pool
          </Link>{" "}
          and earn from the reward budget.
        </p>
      </section>

      {/* pool list */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-medium">Pools</h2>
          <button
            onClick={loadPools}
            className="text-sm text-blue-400 hover:text-blue-300"
          >
            Refresh
          </button>
        </div>

        {loadingPools && (
          <p className="text-sm text-white/50">Loading pools…</p>
        )}

        {!loadingPools && poolsError && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5 text-sm text-amber-300">
            Couldn&apos;t reach the training coordinator right now ({poolsError}).
            It may be offline — try Refresh in a moment.
          </div>
        )}

        {!loadingPools && !poolsError && pools.length === 0 && (
          <div className="rounded-xl border border-white/10 bg-white/5 p-5 text-sm text-white/60">
            No training pools are open yet. Once a pool opens it will show up
            here — or{" "}
            <Link
              href="/mining-onboard"
              className="text-blue-400 hover:text-blue-300"
            >
              spin up a rig
            </Link>{" "}
            to be ready.
          </div>
        )}

        {pools.length > 0 && (
          <div className="grid gap-4 md:grid-cols-2">
            {pools.map((p) => {
              const active = p.pool_id === selectedId;
              return (
                <button
                  key={p.pool_id}
                  onClick={() => setSelectedId(p.pool_id)}
                  className={`rounded-xl border p-5 text-left transition ${
                    active
                      ? "border-blue-500/60 bg-blue-500/5"
                      : "border-white/10 bg-white/5 hover:border-blue-500/40"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="font-medium">
                      {p.name || p.pool_id}
                    </h3>
                    <span className={`text-xs ${statusTone(p.status)}`}>
                      {p.status || "—"}
                    </span>
                  </div>
                  {p.base_model && (
                    <p className="mt-1 truncate text-xs text-white/40">
                      {p.base_model}
                      {p.method ? ` · ${p.method}` : ""}
                    </p>
                  )}
                  <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <dt className="text-white/40">Round</dt>
                      <dd className="text-white/80">{p.round ?? "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-white/40">Shards</dt>
                      <dd className="text-white/80">{p.num_shards ?? "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-white/40">Budget</dt>
                      <dd className="text-white/80">{budgetLabel(p)}</dd>
                    </div>
                  </dl>
                </button>
              );
            })}
          </div>
        )}
      </section>

      {/* selected pool: live status + fund */}
      {selected && (
        <section className="grid gap-6 lg:grid-cols-2">
          {/* live status */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-medium">
                {selected.name || selected.pool_id}
              </h2>
              {loadingStatus && (
                <span className="text-xs text-white/40">refreshing…</span>
              )}
            </div>
            <p className="mt-1 text-xs text-white/40">
              Live status · auto-refreshes every 15s
            </p>

            {statusError && (
              <p className="mt-4 text-sm text-amber-300">{statusError}</p>
            )}

            {status && (
              <dl className="mt-4 space-y-2 text-sm">
                <Row k="Pool ID" v={status.pool_id} mono />
                {status.status != null && (
                  <Row
                    k="Status"
                    v={status.status}
                    tone={statusTone(status.status)}
                  />
                )}
                {status.base_model != null && (
                  <Row k="Base model" v={status.base_model} />
                )}
                {status.method != null && <Row k="Method" v={status.method} />}
                {status.round != null && (
                  <Row k="Round" v={String(status.round)} />
                )}
                {status.num_shards != null && (
                  <Row k="Shards (total)" v={String(status.num_shards)} />
                )}
                {fmtMap(status.shards) && (
                  <Row k="Shards" v={fmtMap(status.shards)} />
                )}
                {status.shards_submitted != null && (
                  <Row
                    k="Shards submitted"
                    v={String(status.shards_submitted)}
                  />
                )}
                {fmtMap(status.contributions) && (
                  <Row k="Contributions" v={fmtMap(status.contributions)} />
                )}
                <Row k="Budget" v={budgetLabel(status)} />
                {status.paid_out_nano != null && (
                  <Row k="Paid out (nano)" v={String(status.paid_out_nano)} />
                )}
                {status.funded != null && (
                  <Row k="Funded" v={status.funded ? "yes" : "no"} />
                )}
                {fmtMap(status.reward_split) && (
                  <Row
                    k="Reward split (bps)"
                    v={fmtMap(status.reward_split)}
                  />
                )}
                {status.served_checkpoint && (
                  <Row
                    k="Served checkpoint"
                    v={`round ${status.served_checkpoint.round ?? "?"} · ${String(
                      status.served_checkpoint.checkpoint_hash || "",
                    ).slice(0, 12)}…`}
                  />
                )}
              </dl>
            )}

            {!status && !statusError && !loadingStatus && (
              <p className="mt-4 text-sm text-white/50">No status available.</p>
            )}
          </div>

          {/* fund */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-6">
            <h2 className="text-lg font-medium">Fund this pool</h2>
            <p className="mt-1 text-sm text-white/60">
              Lock in an ANM reward budget from your Animica wallet. It pays out
              to the people who train, serve, and supply data for this pool.
            </p>

            {/* wallet status */}
            <div className="mt-4 rounded-lg border border-white/10 bg-black/20 p-3 text-sm">
              {hasWallet === false ? (
                <p className="text-white/60">
                  No Animica wallet detected.{" "}
                  <a
                    href="https://animica.org/wallet"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:text-blue-300"
                  >
                    Install it
                  </a>{" "}
                  and reload to fund a pool from here.
                </p>
              ) : account ? (
                <p className="text-white/70">
                  Connected:{" "}
                  <span className="font-mono text-white/90">
                    {account.slice(0, 10)}…{account.slice(-6)}
                  </span>
                </p>
              ) : (
                <div className="flex items-center justify-between gap-3">
                  <span className="text-white/60">
                    {hasWallet === null
                      ? "Detecting wallet…"
                      : "Wallet ready — connect to fund."}
                  </span>
                  <button
                    onClick={connectWallet}
                    className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-500"
                  >
                    Connect wallet
                  </button>
                </div>
              )}
            </div>

            <label className="mt-4 block text-sm">
              Reward (ANM)
              <input
                type="number"
                min={0}
                step="any"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="e.g. 100"
                className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2"
              />
            </label>

            {fundMsg && (
              <p className="mt-3 text-sm text-blue-300">{fundMsg}</p>
            )}
            {fundErr && <p className="mt-3 text-sm text-red-400">{fundErr}</p>}

            <button
              onClick={fund}
              disabled={funding || hasWallet === false}
              className="mt-4 w-full rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-500 disabled:opacity-50"
            >
              {funding ? "Funding…" : "Fund pool"}
            </button>

            <p className="mt-3 text-xs text-white/40">
              You&apos;ll get a quote with the exact amount and a treasury
              address, approve the transfer in your wallet, then this page polls
              until the coordinator confirms it.
            </p>
          </div>
        </section>
      )}
    </div>
  );
}

function Row({
  k,
  v,
  mono,
  tone,
}: {
  k: string;
  v: string;
  mono?: boolean;
  tone?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-2">
      <dt className="text-white/40">{k}</dt>
      <dd
        className={`text-right ${mono ? "font-mono break-all" : ""} ${
          tone || "text-white/80"
        }`}
      >
        {v}
      </dd>
    </div>
  );
}
