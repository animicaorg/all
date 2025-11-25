import * as React from "react";
import { useNetworkState } from "../../state/network";
import { useAccountState } from "../../state/account";
import { useToastState } from "../../state/toasts";
import { sha3_256 } from "../../utils/hash";
import { hexToBytes, bytesToHex } from "../../utils/bytes";

// Prefer re-export from @animica/sdk entry if available; fall back to path import.
import { RandomnessClient } from "@animica/sdk/randomness/client";

/**
 * BeaconPage
 * - Shows current randomness round & latest beacon
 * - Lists recent beacons
 * - Provides commit / reveal helpers (salt + payload)
 *
 * Assumptions:
 * - Node exposes JSON-RPC `rand.getRound`, `rand.getBeacon`, `rand.getHistory`, `rand.commit`, `rand.reveal`
 * - Wallet provider (`window.animica`) is available for address/session (used for display & optional signing)
 */

type RoundInfo = {
  roundId: number;
  openedAt?: number; // unix sec
  commitDeadline?: number; // unix sec
  revealDeadline?: number; // unix sec
  status?: "OPEN" | "REVEAL" | "CLOSED";
};

type BeaconOut = {
  roundId: number;
  beacon: string; // 0x...
  vdfVerified?: boolean;
  finalizedAt?: number; // unix sec
};

type HistoryItem = {
  roundId: number;
  beacon: string;
  finalizedAt?: number;
};

const COMMIT_DOMAIN = "animica/rand/commit:v1";
const REVEAL_DOMAIN = "animica/rand/reveal:v1";

export default function BeaconPage() {
  const { rpcUrl, chainId } = useNetworkState();
  const { address, connect } = useAccountState();
  const { push } = useToastState();

  const client = React.useMemo(() => new RandomnessClient({ url: rpcUrl, chainId }), [rpcUrl, chainId]);

  const [loading, setLoading] = React.useState(false);
  const [round, setRound] = React.useState<RoundInfo | null>(null);
  const [beacon, setBeacon] = React.useState<BeaconOut | null>(null);
  const [history, setHistory] = React.useState<HistoryItem[]>([]);
  const [histPage, setHistPage] = React.useState(0);

  const [saltHex, setSaltHex] = React.useState<string>(randomSalt());
  const [payloadHex, setPayloadHex] = React.useState<string>("0x");
  const [commitBusy, setCommitBusy] = React.useState(false);
  const [revealBusy, setRevealBusy] = React.useState(false);

  React.useEffect(() => {
    void refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, histPage]);

  async function refreshAll() {
    try {
      setLoading(true);
      const [r, b, h] = await Promise.all([
        client.getRound().catch(() => null),
        client.getBeacon().catch(() => null),
        client.getHistory({ offset: histPage * 10, limit: 10 }).catch(() => []),
      ]);
      setRound(r as any);
      setBeacon(b as any);
      setHistory((h as any[]) ?? []);
    } catch (err: any) {
      push({ kind: "error", title: "Failed to load beacon data", body: err?.message || String(err) });
    } finally {
      setLoading(false);
    }
  }

  function onGenerateSalt() {
    const s = randomSalt();
    setSaltHex(s);
  }

  function previewCommitment(): string | null {
    try {
      if (!address) return null;
      const addrBytes = hexToBytes(address);
      const salt = hexToBytes(saltHex || "0x");
      const payload = hexToBytes(payloadHex || "0x");
      const domain = new TextEncoder().encode(COMMIT_DOMAIN);
      const digest = sha3_256(concatBytes(domain, addrBytes, salt, payload));
      return "0x" + bytesToHex(digest);
    } catch {
      return null;
    }
  }

  async function doCommit() {
    if (!address) {
      push({ kind: "warn", title: "Connect wallet", body: "Commit requires a sender address." });
      await connectSafe(connect, push);
      return;
    }
    try {
      setCommitBusy(true);
      // Some networks may require a signature over the tuple; try to produce one if provider supports it.
      const msg = buildTupleMessage(COMMIT_DOMAIN, address, saltHex, payloadHex);
      const signature = await trySign(msg).catch(() => undefined);
      const res = await client.commit({
        from: address,
        salt: saltHex,
        payload: payloadHex,
        signature,
      } as any);
      push({ kind: "success", title: "Commit submitted", body: prettyObj(res) });
      await refreshAll();
    } catch (err: any) {
      push({ kind: "error", title: "Commit failed", body: err?.message || String(err) });
    } finally {
      setCommitBusy(false);
    }
  }

  async function doReveal() {
    if (!address) {
      push({ kind: "warn", title: "Connect wallet", body: "Reveal requires a sender address." });
      await connectSafe(connect, push);
      return;
    }
    try {
      setRevealBusy(true);
      const msg = buildTupleMessage(REVEAL_DOMAIN, address, saltHex, payloadHex);
      const signature = await trySign(msg).catch(() => undefined);
      const res = await client.reveal({
        from: address,
        salt: saltHex,
        payload: payloadHex,
        signature,
      } as any);
      push({ kind: "success", title: "Reveal submitted", body: prettyObj(res) });
      await refreshAll();
    } catch (err: any) {
      push({ kind: "error", title: "Reveal failed", body: err?.message || String(err) });
    } finally {
      setRevealBusy(false);
    }
  }

  const commitPreview = previewCommitment();
  const now = Math.floor(Date.now() / 1000);

  return (
    <div className="p-4 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Randomness Beacon</h1>
          <p className="text-sm text-[color:var(--muted,#6b7280)]">Chain {chainId} · {rpcUrl}</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="px-3 py-1.5 border rounded text-sm" onClick={() => refreshAll()} disabled={loading}>
            Refresh
          </button>
        </div>
      </header>

      {/* Current Round & Beacon */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card title="Current Round">
          {round ? (
            <div className="text-sm space-y-1">
              <KV k="Round ID" v={round.roundId} />
              <KV k="Status" v={round.status ?? statusFromTimes(round, now)} />
              <KV k="Opened" v={fmtTime(round.openedAt)} />
              <KV k="Commit deadline" v={fmtTime(round.commitDeadline)} />
              <KV k="Reveal deadline" v={fmtTime(round.revealDeadline)} />
              <ProgressRow round={round} now={now} />
            </div>
          ) : (
            <Empty msg={loading ? "Loading…" : "No round info available"} />
          )}
        </Card>

        <Card title="Latest Beacon">
          {beacon ? (
            <div className="text-sm space-y-1">
              <KV k="Round ID" v={beacon.roundId} />
              <KV k="Beacon" v={<code className="break-all">{beacon.beacon}</code>} />
              <KV k="VDF verified" v={beacon.vdfVerified ? "yes" : "no"} />
              <KV k="Finalized" v={fmtTime(beacon.finalizedAt)} />
            </div>
          ) : (
            <Empty msg={loading ? "Loading…" : "No beacon yet"} />
          )}
        </Card>
      </section>

      {/* Commit / Reveal */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card title="Commit">
          <form
            className="space-y-3 text-sm"
            onSubmit={(e) => {
              e.preventDefault();
              void doCommit();
            }}
          >
            <KV k="From" v={address ? <code className="break-all">{address}</code> : <i>disconnected</i>} />
            <div>
              <label className="block text-xs mb-1">Salt (hex)</label>
              <div className="flex gap-2">
                <input
                  className="w-full border rounded px-2 py-1 font-mono text-xs"
                  value={saltHex}
                  onChange={(e) => setSaltHex(normalizeHex(e.target.value))}
                  spellCheck={false}
                />
                <button type="button" className="px-2 border rounded" onClick={onGenerateSalt}>
                  Random
                </button>
              </div>
            </div>
            <div>
              <label className="block text-xs mb-1">Payload (hex, optional)</label>
              <input
                className="w-full border rounded px-2 py-1 font-mono text-xs"
                value={payloadHex}
                onChange={(e) => setPayloadHex(normalizeHex(e.target.value))}
                spellCheck={false}
              />
            </div>
            <div className="text-xs">
              Commitment preview:&nbsp;
              {commitPreview ? <code className="break-all">{commitPreview}</code> : <i>—</i>}
            </div>
            <div className="flex gap-2">
              {!address && (
                <button
                  type="button"
                  className="px-3 py-1.5 border rounded"
                  onClick={() => connectSafe(connect, push)}
                >
                  Connect wallet
                </button>
              )}
              <button
                type="submit"
                className="px-3 py-1.5 border rounded"
                disabled={commitBusy}
              >
                {commitBusy ? "Submitting…" : "Commit"}
              </button>
            </div>
          </form>
        </Card>

        <Card title="Reveal">
          <form
            className="space-y-3 text-sm"
            onSubmit={(e) => {
              e.preventDefault();
              void doReveal();
            }}
          >
            <KV k="From" v={address ? <code className="break-all">{address}</code> : <i>disconnected</i>} />
            <div>
              <label className="block text-xs mb-1">Salt (hex)</label>
              <input
                className="w-full border rounded px-2 py-1 font-mono text-xs"
                value={saltHex}
                onChange={(e) => setSaltHex(normalizeHex(e.target.value))}
                spellCheck={false}
              />
            </div>
            <div>
              <label className="block text-xs mb-1">Payload (hex)</label>
              <input
                className="w-full border rounded px-2 py-1 font-mono text-xs"
                value={payloadHex}
                onChange={(e) => setPayloadHex(normalizeHex(e.target.value))}
                spellCheck={false}
              />
            </div>
            <div className="flex gap-2">
              {!address && (
                <button
                  type="button"
                  className="px-3 py-1.5 border rounded"
                  onClick={() => connectSafe(connect, push)}
                >
                  Connect wallet
                </button>
              )}
              <button
                type="submit"
                className="px-3 py-1.5 border rounded"
                disabled={revealBusy}
              >
                {revealBusy ? "Submitting…" : "Reveal"}
              </button>
            </div>
          </form>
        </Card>
      </section>

      {/* History */}
      <section>
        <Card
          title="Recent Beacons"
          extra={
            <div className="flex items-center gap-2">
              <button
                className="px-2 py-1 border rounded text-sm"
                onClick={() => setHistPage((p) => Math.max(0, p - 1))}
                disabled={histPage === 0 || loading}
              >
                Prev
              </button>
              <span className="text-xs text-[color:var(--muted,#6b7280)]">Page {histPage + 1}</span>
              <button
                className="px-2 py-1 border rounded text-sm"
                onClick={() => setHistPage((p) => p + 1)}
                disabled={loading || history.length < 10}
              >
                Next
              </button>
            </div>
          }
        >
          {history.length === 0 ? (
            <Empty msg={loading ? "Loading…" : "No history"} />
          ) : (
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left border-b">
                    <th className="py-2 pr-3">Round</th>
                    <th className="py-2 pr-3">Beacon</th>
                    <th className="py-2">Finalized</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.roundId} className="border-b last:border-b-0">
                      <td className="py-2 pr-3">{h.roundId}</td>
                      <td className="py-2 pr-3">
                        <code className="break-all">{h.beacon}</code>
                      </td>
                      <td className="py-2">{fmtTime(h.finalizedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}

/* --------------------------------- UI bits -------------------------------- */

function Card(props: { title: string; children?: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div className="border rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-medium">{props.title}</h2>
        {props.extra}
      </div>
      {props.children}
    </div>
  );
}

function Empty({ msg }: { msg: string }) {
  return <div className="text-sm text-[color:var(--muted,#6b7280)]">{msg}</div>;
}

function KV({ k, v }: { k: React.ReactNode; v: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <div className="w-36 text-[color:var(--muted,#6b7280)]">{k}</div>
      <div className="flex-1">{v}</div>
    </div>
  );
}

function ProgressRow({ round, now }: { round: RoundInfo; now: number }) {
  const opened = round.openedAt ?? now;
  const cdl = round.commitDeadline ?? now;
  const rdl = round.revealDeadline ?? now;
  const total = Math.max(1, (rdl - opened) || 1);
  const commitPct = clamp01((Math.min(now, cdl) - opened) / total);
  const revealPct = clamp01((Math.min(now, rdl) - cdl) / total);
  return (
    <div className="mt-2">
      <div className="h-2 bg-gray-100 rounded overflow-hidden">
        <div className="h-full bg-blue-300" style={{ width: `${commitPct * 100}%` }} />
        <div className="h-full bg-green-300" style={{ width: `${revealPct * 100}%` }} />
      </div>
      <div className="text-xs text-[color:var(--muted,#6b7280)] mt-1">
        <span className="mr-3">commit window</span>
        <span>reveal window</span>
      </div>
    </div>
  );
}

/* --------------------------------- Helpers -------------------------------- */

function fmtTime(ts?: number) {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

function clamp01(x: number) {
  if (Number.isNaN(x)) return 0;
  return Math.max(0, Math.min(1, x));
}

function statusFromTimes(r: RoundInfo, now: number) {
  const { openedAt, commitDeadline, revealDeadline } = r;
  if (!openedAt || !commitDeadline || !revealDeadline) return "OPEN";
  if (now < commitDeadline) return "OPEN";
  if (now < revealDeadline) return "REVEAL";
  return "CLOSED";
}

function randomSalt(bytes = 32): string {
  const arr = new Uint8Array(bytes);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) {
    crypto.getRandomValues(arr);
  } else {
    // very rare (SSR/tests) – fall back to Math.random (non-crypto)
    for (let i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256);
  }
  return "0x" + bytesToHex(arr);
}

function normalizeHex(s: string): string {
  if (!s) return "0x";
  let t = s.trim();
  if (!t.startsWith("0x") && !t.startsWith("0X")) t = "0x" + t;
  if (t === "0x") return t;
  // remove non-hex
  const body = t.slice(2).replace(/[^0-9a-fA-F]/g, "");
  return "0x" + body;
}

function concatBytes(...parts: Uint8Array[]): Uint8Array {
  let len = 0;
  for (const p of parts) len += p.length;
  const out = new Uint8Array(len);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

function prettyObj(x: any) {
  try {
    return JSON.stringify(x, null, 2);
  } catch {
    return String(x);
  }
}

function buildTupleMessage(domain: string, addrHex: string, saltHex: string, payloadHex: string) {
  // Domain || addr || salt || payload
  const enc = new TextEncoder();
  const msg = concatBytes(enc.encode(domain), hexToBytes(addrHex), hexToBytes(saltHex || "0x"), hexToBytes(payloadHex || "0x"));
  return "0x" + bytesToHex(msg);
}

async function trySign(messageHex: string): Promise<string | undefined> {
  // Try several common provider methods without hard dependency on types
  const anyWin = window as any;
  const provider = anyWin?.animica ?? anyWin?.ethereum;
  if (!provider) return undefined;

  // Preferred: animica_sign with domain-separated bytes
  if (provider.request) {
    try {
      const sig = await provider.request({
        method: "animica_sign",
        params: [{ message: messageHex }],
      });
      if (typeof sig === "string") return sig;
    } catch {
      // ignore and try alternatives
    }
  }

  // Fallback no-op (network may not require a signature for commit/reveal)
  return undefined;
}

async function connectSafe(connect: () => Promise<void>, push: (t: any) => void) {
  try {
    await connect();
  } catch (err: any) {
    push({ kind: "error", title: "Connect failed", body: err?.message || String(err) });
  }
}
