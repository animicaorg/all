import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { classNames } from "../../utils/classnames";
import { ago } from "../../utils/time";
import { shortHash } from "../../utils/format";

// -------------------------------------------------------------------------------------
// Types (lenient: tolerate differing backends/field names)
// -------------------------------------------------------------------------------------
type Peer = {
  id: string;            // peer id / pubkey
  addr?: string;         // multiaddr or host:port
  client?: string;       // agent string
  height?: number;       // best block height
  latencyMs?: number;    // latest RTT
  topics?: string[];     // gossipsub topics
  direction?: "in" | "out" | "inout";
  score?: number;
  gossipOk?: boolean;    // healthy gossip flow?
};

type GossipStats = {
  meshPeers?: number;
  meshTopics?: number;
  rxPerSec?: number;     // messages/sec received
  txPerSec?: number;     // messages/sec sent
  dupRate?: number;      // duplicate fraction 0..1
  invalidRate?: number;  // invalid fraction 0..1
  lastUpdate?: number;   // ms epoch
};

type HealthServiceT = {
  listPeers?: () => Promise<any>;
  getPeers?: () => Promise<any>;
  peers?: () => Promise<any>;
  gossipStats?: () => Promise<any>;
  getGossipStats?: () => Promise<any>;
  getHealth?: () => Promise<any>;
};

// Lazy import to avoid tight coupling; fall back gracefully if shapes differ.
const HealthService = (async () => {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod: HealthServiceT = await import("../../services/health");
    return mod as HealthServiceT;
  } catch {
    return {} as HealthServiceT;
  }
})();

// -------------------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------------------
const REFRESH_MS = 10_000;
const HISTORY_SAMPLES = 60;

function n(x: any): number | undefined {
  if (x === null || x === undefined) return undefined;
  if (typeof x === "number") return Number.isFinite(x) ? x : undefined;
  if (typeof x === "string") {
    const asNum = Number(x);
    if (Number.isFinite(asNum)) return asNum;
  }
  if (typeof x === "bigint") return Number(x);
  return undefined;
}

function normalizePeer(p: any): Peer {
  const id = String(
    p?.id ?? p?.peerId ?? p?.peer_id ?? p?.pubkey ?? p?.nodeId ?? "unknown"
  );
  const addr =
    p?.addr ?? p?.address ?? p?.multiaddr ?? p?.multi_addr ?? p?.host;
  const client = p?.client ?? p?.agent ?? p?.agentVersion ?? p?.name;
  const height =
    n(p?.height ?? p?.bestHeight ?? p?.blockHeight ?? p?.best)?.valueOf();
  const latencyMs =
    n(p?.latencyMs ?? p?.latency_ms ?? p?.latency ?? p?.rttMs) ?? undefined;
  const topics =
    (Array.isArray(p?.topics) && p.topics) ||
    (Array.isArray(p?.subscribed) && p.subscribed) ||
    (typeof p?.topics === "object" && Object.keys(p.topics)) ||
    [];
  const direction =
    (p?.direction as Peer["direction"]) ||
    (p?.inbound && p?.outbound
      ? "inout"
      : p?.inbound
      ? "in"
      : p?.outbound
      ? "out"
      : undefined);
  const score = n(p?.score ?? p?.reputation ?? p?.gossipScore);
  const gossipOk =
    typeof p?.gossipOk === "boolean"
      ? p.gossipOk
      : typeof p?.healthy === "boolean"
      ? p.healthy
      : undefined;

  return { id, addr, client, height, latencyMs, topics, direction, score, gossipOk };
}

function normalizeGossip(x: any): GossipStats {
  if (!x) return {};
  return {
    meshPeers: n(x?.meshPeers ?? x?.mesh_peers ?? x?.peers),
    meshTopics: n(x?.meshTopics ?? x?.mesh_topics ?? (Array.isArray(x?.topics) ? x.topics.length : undefined)),
    rxPerSec: n(x?.rxPerSec ?? x?.ingressRate ?? x?.rx_rate),
    txPerSec: n(x?.txPerSec ?? x?.egressRate ?? x?.tx_rate),
    dupRate: n(x?.dupRate ?? x?.duplicateRate ?? x?.dup_rate),
    invalidRate: n(x?.invalidRate ?? x?.invalid_rate),
    lastUpdate: n(x?.lastUpdate ?? x?.timestamp),
  };
}

function fmtPct(x?: number) {
  if (!Number.isFinite(x || NaN)) return "—";
  return `${Math.round((x as number) * 100)}%`;
}

function fmtMs(x?: number) {
  if (!Number.isFinite(x || NaN)) return "—";
  const v = x as number;
  if (v < 1000) return `${Math.round(v)} ms`;
  return `${(v / 1000).toFixed(v >= 10_000 ? 0 : 1)} s`;
}

function avg(arr: number[]) {
  if (!arr.length) return undefined;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

// Color for RTT (ms)
function rttColor(ms?: number) {
  if (!Number.isFinite(ms || NaN)) return "var(--bg-3)";
  const v = ms as number;
  if (v <= 40) return "var(--green-600)";
  if (v <= 80) return "var(--green-500)";
  if (v <= 150) return "var(--yellow-600)";
  if (v <= 300) return "var(--orange-600)";
  if (v <= 600) return "var(--red-600)";
  return "var(--red-700)";
}

// -------------------------------------------------------------------------------------
// Page
// -------------------------------------------------------------------------------------
export default function NetworkPage() {
  const [peers, setPeers] = useState<Peer[]>([]);
  const [gossip, setGossip] = useState<GossipStats>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | undefined>();

  // RTT history per peer (for "heatmap" rows)
  const historyRef = useRef<Map<string, number[]>>(new Map());
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(undefined);
    try {
      const svc = await HealthService;
      let list: any =
        (await svc.listPeers?.()) ??
        (await svc.getPeers?.()) ??
        (await svc.peers?.()) ??
        [];

      // Some APIs return {peers: [...]}
      if (!Array.isArray(list) && Array.isArray(list?.peers)) list = list.peers;

      const normalized: Peer[] = (Array.isArray(list) ? list : []).map(normalizePeer);

      // maintain RTT history
      const map = historyRef.current;
      for (const p of normalized) {
        const key = p.id || p.addr || String(Math.random());
        const buf = map.get(key) ?? [];
        if (typeof p.latencyMs === "number") buf.push(p.latencyMs);
        if (buf.length > HISTORY_SAMPLES) buf.splice(0, buf.length - HISTORY_SAMPLES);
        map.set(key, buf);
      }
      // prune stale
      for (const k of Array.from(map.keys())) {
        if (!normalized.find((p) => p.id === k)) map.delete(k);
      }

      setPeers(normalized);

      // gossip stats (best-effort)
      const gRaw =
        (await svc.gossipStats?.()) ??
        (await svc.getGossipStats?.()) ??
        (await svc.getHealth?.()) ??
        null;
      if (gRaw) setGossip(normalizeGossip(gRaw));
    } catch (e: any) {
      setErr(e?.message || String(e));
      setPeers([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  const stats = useMemo(() => {
    const count = peers.length;
    const withRtt = peers.filter((p) => typeof p.latencyMs === "number");
    const avgRtt = avg(withRtt.map((p) => p.latencyMs!));
    const unhealthy = peers.filter((p) => p.gossipOk === false).length;
    const inbound = peers.filter((p) => p.direction === "in" || p.direction === "inout").length;
    const outbound = peers.filter((p) => p.direction === "out" || p.direction === "inout").length;
    return { count, avgRtt, unhealthy, inbound, outbound };
  }, [peers]);

  return (
    <section className="page">
      <header className="page-header">
        <div className="row wrap items-end gap-12">
          <h1>Network</h1>
          {err ? <div className="alert warn">{err}</div> : null}
          <button className="btn small" onClick={() => void refresh()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        <div className="grid cols-5 gap-12 mt-3">
          <StatCard label="Peers" value={stats.count} />
          <StatCard label="Avg RTT" value={fmtMs(stats.avgRtt)} />
          <StatCard label="Unhealthy" value={stats.unhealthy} warn={stats.unhealthy > 0} />
          <StatCard label="Inbound" value={stats.inbound} />
          <StatCard label="Outbound" value={stats.outbound} />
        </div>

        <div className="panel mt-3">
          <div className="row space-between items-center">
            <h3 className="m-0">Gossip Health</h3>
            <span className="dim small">
              {gossip.lastUpdate ? `Updated ${ago(gossip.lastUpdate)}` : "—"}
            </span>
          </div>
          <div className="grid cols-5 gap-12 mt-2">
            <KV label="Mesh Peers" value={gossip.meshPeers ?? "—"} />
            <KV label="Mesh Topics" value={gossip.meshTopics ?? "—"} />
            <KV label="Ingress" value={Number.isFinite(gossip.rxPerSec || NaN) ? `${gossip.rxPerSec!.toFixed(2)}/s` : "—"} />
            <KV label="Egress" value={Number.isFinite(gossip.txPerSec || NaN) ? `${gossip.txPerSec!.toFixed(2)}/s` : "—"} />
            <KV label="Duplicates" value={fmtPct(gossip.dupRate)} />
          </div>
          <div className="grid cols-2 gap-16 mt-2">
            <Gauge label="Invalid Rate" value={gossip.invalidRate} />
            <Legend />
          </div>
        </div>
      </header>

      <section className="mt-4">
        <h3>RTT Heatmap (last {HISTORY_SAMPLES} samples)</h3>
        <Heatmap
          peers={peers}
          history={historyRef.current}
          samples={HISTORY_SAMPLES}
        />
      </section>

      <div className="table-wrap mt-3">
        <table className="table">
          <thead>
            <tr>
              <th>Peer</th>
              <th>Direction</th>
              <th>Client</th>
              <th>Address</th>
              <th>Height</th>
              <th>RTT</th>
              <th>Topics</th>
              <th className="right">Gossip</th>
            </tr>
          </thead>
          <tbody>
            {peers.map((p) => (
              <tr key={p.id}>
                <td className="mono" title={p.id}>{shortHash(p.id, 12)}</td>
                <td>
                  <span className={classNames("tag", p.direction === "in" && "info", p.direction === "out" && "accent")}>
                    {p.direction ?? "—"}
                  </span>
                </td>
                <td title={p.client}>{p.client || "—"}</td>
                <td className="mono" title={p.addr || undefined}>{p.addr ? shortHash(p.addr, 18) : "—"}</td>
                <td className="mono">{Number.isFinite(p.height || NaN) ? p.height : "—"}</td>
                <td className="mono" title={p.latencyMs ? `${p.latencyMs.toFixed(0)} ms` : undefined}>
                  {fmtMs(p.latencyMs)}
                </td>
                <td>
                  {p.topics?.length ? (
                    <span className="dim small">{p.topics.length} topics</span>
                  ) : "—"}
                </td>
                <td className="right">
                  <span
                    className={classNames(
                      "tag",
                      p.gossipOk === false ? "warn" : "success"
                    )}
                  >
                    {p.gossipOk === false ? "Unhealthy" : "Healthy"}
                  </span>
                </td>
              </tr>
            ))}
            {!peers.length && !loading ? (
              <tr>
                <td className="center dim" colSpan={8}>No peers.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// -------------------------------------------------------------------------------------
// Components
// -------------------------------------------------------------------------------------
function StatCard({ label, value, warn }: { label: string; value: React.ReactNode; warn?: boolean }) {
  return (
    <div className={classNames("panel", warn && "tone-warn")}>
      <div className="dim small">{label}</div>
      <div className="h2 mt-1">{value}</div>
    </div>
  );
}

function KV({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="dim small">{label}</div>
      <div className="mt-1">{value}</div>
    </div>
  );
}

function Gauge({ label, value }: { label: string; value?: number }) {
  const pct = Number.isFinite(value || NaN) ? Math.max(0, Math.min(1, value as number)) : undefined;
  return (
    <div className="panel">
      <div className="dim small">{label}</div>
      <div className="mt-2">
        <div className="gauge">
          <div className="bar" style={{ width: pct !== undefined ? `${pct * 100}%` : "0%" }} />
        </div>
        <div className="mt-1">{pct !== undefined ? fmtPct(pct) : "—"}</div>
      </div>
      <style jsx>{`
        .gauge { width: 100%; height: 12px; background: var(--bg-3); border-radius: 999px; overflow: hidden; }
        .bar { height: 100%; background: var(--red-600); }
      `}</style>
    </div>
  );
}

function Legend() {
  return (
    <div className="panel">
      <div className="dim small">RTT Color Scale</div>
      <div className="row gap-8 mt-2 items-center">
        <Swatch color="var(--green-600)" label="≤40ms" />
        <Swatch color="var(--green-500)" label="≤80ms" />
        <Swatch color="var(--yellow-600)" label="≤150ms" />
        <Swatch color="var(--orange-600)" label="≤300ms" />
        <Swatch color="var(--red-600)" label="≤600ms" />
        <Swatch color="var(--red-700)" label=">600ms" />
      </div>
    </div>
  );
}

function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <div className="row gap-6 items-center">
      <div style={{ width: 16, height: 16, background: color, borderRadius: 4, border: "1px solid var(--border)" }} />
      <span className="small">{label}</span>
    </div>
  );
}

function Heatmap({
  peers,
  history,
  samples,
}: {
  peers: Peer[];
  history: Map<string, number[]>;
  samples: number;
}) {
  if (!peers.length) return <div className="dim">No data.</div>;

  return (
    <div className="heatmap">
      <div className="hm-grid">
        {peers.map((p) => {
          const hist = history.get(p.id) || [];
          const row = Array.from({ length: samples })
            .map((_, i) => hist[hist.length - samples + i]) // may be undefined
            .map((v) => (Number.isFinite(v || NaN) ? (v as number) : undefined));
          return (
            <div key={p.id} className="hm-row">
              <div className="hm-meta mono" title={p.id}>{shortHash(p.id, 12)}</div>
              <div className="hm-cells">
                {row.map((v, i) => (
                  <div
                    key={i}
                    className="hm-cell"
                    title={v !== undefined ? `${v.toFixed(0)} ms` : "no sample"}
                    style={{ background: rttColor(v) }}
                  />
                ))}
              </div>
              <div className="hm-rt mono" title={p.latencyMs ? `${p.latencyMs.toFixed(0)} ms` : undefined}>
                {fmtMs(p.latencyMs)}
              </div>
            </div>
          );
        })}
      </div>

      <style jsx>{`
        .heatmap { width: 100%; overflow-x: auto; }
        .hm-grid { display: flex; flex-direction: column; gap: 6px; }
        .hm-row { display: grid; grid-template-columns: 120px 1fr 96px; align-items: center; gap: 8px; }
        .hm-meta { color: var(--text-2); }
        .hm-cells { display: grid; grid-template-columns: repeat(${samples}, minmax(6px, 1fr)); gap: 2px; }
        .hm-cell { height: 12px; border-radius: 2px; border: 1px solid var(--border); }
        .hm-rt { text-align: right; }
        @media (max-width: 920px) {
          .hm-row { grid-template-columns: 90px 1fr 72px; }
          .hm-cells { grid-template-columns: repeat(${Math.max(30, Math.floor(samples/2))}, minmax(6px, 1fr)); }
        }
      `}</style>
    </div>
  );
}
