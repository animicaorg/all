import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { classNames } from "../../utils/classnames";
import { ago } from "../../utils/time";
import { shortHash } from "../../utils/format";
import * as BeaconService from "../../services/beacon";

// -------------------------------------------------------------------------------------
// Types & Normalizers (lenient to tolerate differing backends)
// -------------------------------------------------------------------------------------
type VdfInfo = {
  verified?: boolean;
  verifier?: string;
  proof?: any;
  elapsedMs?: number;
};

type RoundItem = {
  round: number;
  startedAt?: number;   // ms since epoch
  sealedAt?: number;    // ms since epoch
  beacon?: string;      // hex
  entropy?: string;     // hex
  vdf?: VdfInfo;
};

type RoundsQuery = {
  fromRound?: number;
  toRound?: number;
  limit?: number;
  offset?: number;
};

function n(x: any): number | undefined {
  if (x === null || x === undefined) return undefined;
  if (typeof x === "number") return Number.isFinite(x) ? x : undefined;
  if (typeof x === "string") {
    const asNum = Number(x);
    if (Number.isFinite(asNum)) return asNum;
    // hex timestamp or bigint
    if (/^0x[0-9a-fA-F]+$/.test(x)) {
      try { return Number(BigInt(x)); } catch { return undefined; }
    }
  }
  if (typeof x === "bigint") return Number(x);
  return undefined;
}

function normalizeRound(x: any): RoundItem {
  const round = n(x?.round ?? x?.id ?? x?.height) ?? 0;
  const startedAt = n(x?.startedAt ?? x?.start ?? x?.timestampStart ?? x?.timeStart);
  const sealedAt  = n(x?.sealedAt ?? x?.end   ?? x?.timestampEnd   ?? x?.timeEnd ?? x?.timestamp);
  const beacon    = x?.beacon ?? x?.value ?? x?.hash ?? x?.output;
  const entropy   = x?.entropy ?? x?.seed ?? x?.randomness;

  let vdf: VdfInfo | undefined;
  if (x?.vdf || x?.vdfProof || x?.proof) {
    const v = x?.vdf ?? {};
    vdf = {
      verified: !!(x?.verified ?? v?.verified ?? x?.vdfVerified),
      verifier: v?.verifier ?? x?.verifier,
      proof: x?.proof ?? x?.vdfProof ?? v?.proof,
      elapsedMs: n(x?.elapsedMs ?? v?.elapsedMs ?? v?.elapsed),
    };
  }

  return { round, startedAt, sealedAt, beacon, entropy, vdf };
}

// -------------------------------------------------------------------------------------
// UI helpers
// -------------------------------------------------------------------------------------
const REFRESH_MS = 30_000;

function fmtInt(nv?: number) {
  if (!Number.isFinite(nv || NaN)) return "—";
  const v = nv as number;
  if (Math.abs(v) >= 1_000_000_000) return (v / 1_000_000_000).toFixed(2) + "B";
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M";
  if (Math.abs(v) >= 10_000) return Math.round(v / 1_000) + "k";
  return String(Math.round(v));
}

function ms(v?: number) {
  if (!Number.isFinite(v || NaN)) return "—";
  const x = v as number;
  if (x < 1000) return `${x|0} ms`;
  const s = x/1000;
  if (s < 60) return `${s.toFixed(s >= 10 ? 0 : 1)} s`;
  const m = Math.floor(s/60);
  const rs = Math.round(s % 60);
  return `${m}m ${rs}s`;
}

// -------------------------------------------------------------------------------------
// Page Component
// -------------------------------------------------------------------------------------
export default function BeaconPage() {
  const [rounds, setRounds] = useState<RoundItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | undefined>();
  const [page, setPage] = useState(0);
  const pageSize = 50;
  const [latest, setLatest] = useState<RoundItem | null>(null);
  const [proofView, setProofView] = useState<{ round: number; json: any } | null>(null);
  const [verifying, setVerifying] = useState<number | null>(null);

  const timer = useRef<number | undefined>(undefined);

  const fetchLatest = useCallback(async () => {
    try {
      const r =
        (await (BeaconService as any).getLatest?.()) ??
        (await (BeaconService as any).latest?.()) ??
        (await (BeaconService as any).getLatestBeacon?.());
      if (r) setLatest(normalizeRound(r));
    } catch {
      // non-fatal
    }
  }, []);

  const refresh = useCallback(async () => {
    setErr(undefined);
    setLoading(true);
    try {
      const q: RoundsQuery = {
        limit: pageSize,
        offset: page * pageSize,
      };

      let resp: any =
        (await (BeaconService as any).listRounds?.(q)) ??
        (await (BeaconService as any).getRounds?.(q)) ??
        (await (BeaconService as any).fetchRounds?.(q)) ??
        [];

      // support {items,total}
      if (resp && Array.isArray(resp.items)) resp = resp.items;

      const items: RoundItem[] = (Array.isArray(resp) ? resp : []).map(normalizeRound);
      // sort desc by round if server didn't
      items.sort((a, b) => b.round - a.round);
      setRounds(items);
    } catch (e: any) {
      setErr(e?.message || String(e));
      setRounds([]);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize]);

  useEffect(() => {
    void refresh();
    void fetchLatest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(() => {
      void refresh();
      void fetchLatest();
    }, REFRESH_MS);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh, fetchLatest]);

  const stats = useMemo(() => {
    const count = rounds.length;
    const verified = rounds.filter(r => !!r.vdf?.verified).length;
    const ratio = count ? (verified / count) : 0;
    const lastRound = rounds[0]?.round;
    return { count, verified, ratio, lastRound };
  }, [rounds]);

  const verifyRound = useCallback(async (r: RoundItem) => {
    setVerifying(r.round);
    try {
      const arg = r.round ?? r;
      const proof =
        (await (BeaconService as any).verifyRound?.(arg)) ??
        (await (BeaconService as any).verifyVdf?.(arg)) ??
        (await (BeaconService as any).verifyVDF?.(arg)) ??
        (await (BeaconService as any).verify?.(arg)) ??
        null;

      // When services return a status like { verified: true/false, proof, elapsedMs }
      const upd = { ...r, vdf: { ...(r.vdf || {}), ...(proof || {}) } };
      setRounds(prev => prev.map(x => (x.round === r.round ? normalizeRound(upd) : x)));
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setVerifying(null);
    }
  }, []);

  // pick a "current" for the header: prefer latest() else top of rounds
  const current = latest ?? (rounds.length ? rounds[0] : null);

  return (
    <section className="page">
      <header className="page-header">
        <div className="row wrap gap-12 items-end">
          <h1>Randomness Beacon</h1>
          {err ? <div className="alert warn">{err}</div> : null}
        </div>

        <div className="grid cols-4 gap-12 mt-3">
          <StatCard label="Rounds loaded" value={fmtInt(stats.count)} />
          <StatCard label="VDF verified" value={`${fmtInt(stats.verified)} (${Math.round(stats.ratio * 100)}%)`} />
          <StatCard label="Latest round" value={fmtInt(stats.lastRound)} />
          <StatCard label="Auto-refresh" value="30s" />
        </div>

        <div className="panel mt-3">
          <div className="row space-between items-center">
            <h3 className="m-0">Latest Beacon</h3>
            <button className="btn tiny" onClick={() => { void refresh(); void fetchLatest(); }} disabled={loading}>
              Refresh
            </button>
          </div>
          {current ? (
            <div className="grid cols-2 gap-16 mt-2">
              <div>
                <KV label="Round" value={current.round} mono />
                <KV label="Beacon" value={current.beacon ? shortHash(current.beacon, 14) : "—"} title={current.beacon} mono />
                <KV label="Entropy" value={current.entropy ? shortHash(current.entropy, 14) : "—"} title={current.entropy} mono />
                <KV
                  label="Started"
                  value={current.startedAt ? ago(current.startedAt) : "—"}
                  title={current.startedAt ? new Date(current.startedAt).toISOString() : undefined}
                />
                <KV
                  label="Sealed"
                  value={current.sealedAt ? ago(current.sealedAt) : "—"}
                  title={current.sealedAt ? new Date(current.sealedAt).toISOString() : undefined}
                />
              </div>
              <div>
                <KV label="VDF Verified" value={current.vdf?.verified ? "Yes" : "No"} />
                <KV label="Verifier" value={current.vdf?.verifier || "—"} />
                <KV label="Verify Time" value={ms(current.vdf?.elapsedMs)} />
                <div className="row gap-8 mt-2">
                  <button
                    className={classNames("btn", "small", current.vdf?.verified && "outline")}
                    onClick={() => verifyRound(current)}
                    disabled={verifying === current.round}
                  >
                    {verifying === current.round ? "Verifying…" : current.vdf?.verified ? "Re-Verify" : "Verify"}
                  </button>
                  {current.vdf?.proof ? (
                    <button className="btn small" onClick={() => setProofView({ round: current.round, json: current.vdf?.proof })}>
                      View Proof
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
          ) : (
            <div className="dim">No beacon yet.</div>
          )}
        </div>
      </header>

      <section className="mt-4">
        <h3>Rounds Timeline</h3>
        <RoundTimeline items={rounds} currentRound={current?.round} onClickRound={(r) => {
          const found = rounds.find(x => x.round === r);
          if (found?.vdf?.proof) setProofView({ round: r, json: found.vdf.proof });
        }} />
      </section>

      <div className="table-wrap mt-3">
        <table className="table">
          <thead>
            <tr>
              <th>Round</th>
              <th>Beacon</th>
              <th>Started</th>
              <th>Sealed</th>
              <th>VDF</th>
              <th className="right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rounds.map((r) => (
              <tr key={r.round}>
                <td className="mono">{r.round}</td>
                <td className="mono" title={r.beacon || undefined}>{r.beacon ? shortHash(r.beacon, 14) : "—"}</td>
                <td title={r.startedAt ? new Date(r.startedAt).toISOString() : undefined}>{r.startedAt ? ago(r.startedAt) : "—"}</td>
                <td title={r.sealedAt ? new Date(r.sealedAt).toISOString() : undefined}>{r.sealedAt ? ago(r.sealedAt) : "—"}</td>
                <td>
                  <span className={classNames("tag", r.vdf?.verified ? "success" : "warn")}>
                    {r.vdf?.verified ? "Verified" : "Unverified"}
                  </span>
                  {r.vdf?.elapsedMs ? <span className="dim small ml-2">{ms(r.vdf.elapsedMs)}</span> : null}
                </td>
                <td className="right">
                  <div className="row gap-6 justify-end">
                    <button className="btn tiny" onClick={() => verifyRound(r)} disabled={verifying === r.round}>
                      {verifying === r.round ? "…" : r.vdf?.verified ? "Re-Verify" : "Verify"}
                    </button>
                    {r.vdf?.proof ? (
                      <button className="btn tiny" onClick={() => setProofView({ round: r.round, json: r.vdf?.proof })}>
                        Proof
                      </button>
                    ) : (
                      <button className="btn tiny outline" onClick={() => setProofView({ round: r.round, json: { note: "No proof available" } })}>
                        Proof
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {!rounds.length && !loading ? (
              <tr>
                <td className="center dim" colSpan={6}>No rounds loaded.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="row gap-10 items-center mt-3">
        <button className="btn small" onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>Prev</button>
        <span className="dim small">Page {page + 1}</span>
        <button className="btn small" onClick={() => setPage(p => p + 1)} disabled={rounds.length < pageSize}>Next</button>
        {loading ? <span className="dim small">Loading…</span> : null}
      </div>

      <ProofModal view={proofView} onClose={() => setProofView(null)} />
    </section>
  );
}

// -------------------------------------------------------------------------------------
// Components
// -------------------------------------------------------------------------------------
function KV({ label, value, title, mono }: { label: string; value: React.ReactNode; title?: string; mono?: boolean }) {
  return (
    <div className="row gap-8 mt-1 items-baseline">
      <div className="dim small" style={{ minWidth: 96 }}>{label}</div>
      <div className={classNames(mono && "mono")} title={title}>{value}</div>
    </div>
  );
}

function RoundTimeline({
  items,
  currentRound,
  onClickRound,
}: {
  items: RoundItem[];
  currentRound?: number;
  onClickRound?: (round: number) => void;
}) {
  if (!items.length) return <div className="dim">No data.</div>;

  const minR = items[items.length - 1].round;
  const maxR = items[0].round;

  return (
    <div className="timeline-wrap">
      <div className="timeline" role="list">
        {items.map((r) => {
          const verified = !!r.vdf?.verified;
          const isCurrent = currentRound === r.round;
          return (
            <button
              key={r.round}
              role="listitem"
              className={classNames("dot", verified ? "ok" : "wait", isCurrent && "current")}
              title={`Round ${r.round} • ${verified ? "VDF Verified" : "Unverified"}${r.sealedAt ? ` • ${ago(r.sealedAt)}` : ""}`}
              onClick={() => onClickRound?.(r.round)}
            />
          );
        })}
      </div>
      <div className="row space-between dim small mt-1">
        <span>r={minR}</span>
        <span>r={maxR}</span>
      </div>

      <style jsx>{`
        .timeline-wrap { overflow-x: auto; padding: 6px 0; }
        .timeline { display: flex; gap: 8px; align-items: center; min-height: 20px; }
        .dot { width: 12px; height: 12px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg-2); }
        .dot.ok { background: var(--green-600); border-color: var(--green-700); }
        .dot.wait { background: var(--yellow-600); border-color: var(--yellow-700); }
        .dot.current { outline: 2px solid var(--accent); outline-offset: 2px; }
        .timeline button { min-width: 12px; }
      `}</style>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="panel">
      <div className="dim small">{label}</div>
      <div className="h2 mt-1">{value}</div>
    </div>
  );
}

function ProofModal({
  view,
  onClose,
}: {
  view: { round: number; json: any } | null;
  onClose: () => void;
}) {
  if (!view) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 800 }}>
        <header className="row space-between items-center">
          <h3 className="m-0">VDF Proof — Round {view.round}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <pre className="code mt-2" style={{ maxHeight: 420, overflow: "auto" }}>
{JSON.stringify(view.json, null, 2)}
        </pre>
        <footer className="row justify-end mt-2">
          <button className="btn" onClick={onClose}>Close</button>
        </footer>
      </div>
    </div>
  );
}
