/**
 * Animica Explorer — P2P peers/latency snapshot state
 * -----------------------------------------------------------------------------
 * Tracks connected peers, rolling RTT samples, EWMA latency, jitter, and loss.
 * Accepts heterogeneous snapshots from different node RPCs and WS feeds.
 *
 * Normalized peer fields (best-effort):
 *  - id:            peer.id | peer.peerId | peer.nodeId | peer.pubkey
 *  - address:       peer.address | peer.addr | peer.remoteAddress
 *  - agent:         peer.agent | peer.client | peer.userAgent
 *  - version:       peer.version | peer.protocolVersion
 *  - direction:     peer.direction ('in'|'out') | peer.isInbound
 *  - height:        peer.height | peer.head | peer.blockNumber
 *  - headHash:      peer.hash | peer.headHash
 *  - rtt:           peer.rtt | peer.latency | peer.ping (ms)
 *  - geo:           peer.geo {country, region, city} if supplied by backend
 */

import { create } from 'zustand';
import { shallow } from 'zustand/shallow';

// -------------------------------- Types -------------------------------------

export type PeerId = string;

export interface GeoInfo {
  country?: string;
  region?: string;
  city?: string;
}

export interface PeerStats {
  capacity: number;     // ring capacity for RTTs
  samples: number[];    // ring buffer of RTT ms (latest at end)
  sentPings: number;    // total pings sent
  recvPongs: number;    // pongs received
  lastRtt?: number;     // last RTT sample (ms)
  ewmaRtt?: number;     // exponentially weighted moving average (ms)
  jitter?: number;      // mean absolute diff between consecutive RTTs (ms)
  loss?: number;        // (sent - recv)/sent in [0..1]
}

export interface Peer {
  id: PeerId;
  address?: string;
  agent?: string;
  version?: string;
  protocols?: string[];
  direction?: 'in' | 'out';
  height?: number;
  headHash?: string;

  connectedAt?: number; // ms epoch
  lastSeenAt?: number;  // ms epoch

  geo?: GeoInfo;

  stats: PeerStats;
  tags?: string[];
}

export interface PeersSummary {
  total: number;
  connected: number;
  inPeers: number;
  outPeers: number;
  avgRtt?: number;
  medianRtt?: number;
  avgHeight?: number;
}

export interface PeersConfig {
  rttCapacity?: number; // default 64
  ewmaAlpha?: number;   // default 0.25
  staleMs?: number;     // default 120_000 (2 minutes)
}

export interface PeersState {
  peers: Record<PeerId, Peer>;
  order: PeerId[]; // sorted by ewmaRtt asc (then lastSeen desc)
  summary: PeersSummary;

  // config
  rttCapacity: number;
  ewmaAlpha: number;
  staleMs: number;

  // mutators
  reset: () => void;
  configure: (cfg: PeersConfig) => void;
  upsertPeer: (p: Partial<Peer> & { id: PeerId }) => void;
  removePeer: (id: PeerId) => void;
  pruneStale: () => void;

  ingestSnapshot: (payload: any) => void; // any shape, best-effort normalize
  recordPing: (id: PeerId) => void;       // increments sent
  markPong: (id: PeerId, rttMs: number) => void; // add RTT sample
  markFailedPing: (id: PeerId) => void;   // increments sent (no pong)

  // selectors
  list: () => Peer[];
  topByLatency: (limit?: number) => Peer[];
  byId: (id: PeerId) => Peer | undefined;
}

// ------------------------------ Utilities -----------------------------------

const DEFAULT_RTT_CAP = 64;
const DEFAULT_ALPHA = 0.25;
const DEFAULT_STALE_MS = 2 * 60_000;

function now(): number {
  return Date.now();
}

function toInt(x: any): number | undefined {
  const n = Number(x);
  return Number.isFinite(n) ? (n | 0) : undefined;
}

function toMs(x: any): number | undefined {
  if (x == null) return undefined;
  const n = Number(x);
  return Number.isFinite(n) ? n : undefined;
}

function boolToDir(isInbound: any | undefined): 'in' | 'out' | undefined {
  if (typeof isInbound === 'boolean') return isInbound ? 'in' : 'out';
  return undefined;
}

function median(nums: number[]): number | undefined {
  const arr = nums.filter(Number.isFinite);
  if (arr.length === 0) return undefined;
  arr.sort((a, b) => a - b);
  const mid = Math.floor(arr.length / 2);
  return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

function mean(nums: number[]): number | undefined {
  const arr = nums.filter(Number.isFinite);
  if (arr.length === 0) return undefined;
  const sum = arr.reduce((a, b) => a + b, 0);
  return sum / arr.length;
}

function ewma(prev: number | undefined, sample: number, alpha: number): number {
  if (!Number.isFinite(prev as number)) return sample;
  return alpha * sample + (1 - alpha) * (prev as number);
}

function computeJitter(samples: number[]): number | undefined {
  if (samples.length < 2) return undefined;
  let sum = 0;
  for (let i = 1; i < samples.length; i++) {
    sum += Math.abs(samples[i] - samples[i - 1]);
  }
  return sum / (samples.length - 1);
}

function pushRing(arr: number[], value: number, capacity: number) {
  arr.push(value);
  if (arr.length > capacity) arr.splice(0, arr.length - capacity);
}

function extractPeerShape(raw: any): Omit<Peer, 'stats'> & { stats?: Partial<PeerStats> } {
  // Basic fields
  const id =
    raw?.id ??
    raw?.peerId ??
    raw?.nodeId ??
    raw?.pubkey ??
    raw?.publicKey ??
    raw?.enr ??
    raw?.peer_id;

  const address = raw?.address ?? raw?.addr ?? raw?.remoteAddress ?? raw?.multiaddr ?? raw?.multiAddr;
  const agent = raw?.agent ?? raw?.client ?? raw?.userAgent;
  const version = raw?.version ?? raw?.protocolVersion;

  const direction =
    raw?.direction ??
    (raw?.isInbound !== undefined ? (raw?.isInbound ? 'in' : 'out') : undefined) ??
    boolToDir(raw?.inbound);

  const height = toInt(raw?.height ?? raw?.head ?? raw?.blockNumber ?? raw?.bestHeight ?? raw?.chainHeight);
  const headHash = raw?.hash ?? raw?.headHash ?? raw?.bestHash;

  const rtt =
    toMs(raw?.rtt ?? raw?.latency ?? raw?.ping ?? raw?.rttMs ?? raw?.latencyMs);

  const geo: GeoInfo | undefined = raw?.geo
    ? {
        country: raw?.geo?.country ?? raw?.geo?.cc,
        region: raw?.geo?.region,
        city: raw?.geo?.city,
      }
    : undefined;

  const protocols: string[] | undefined =
    (Array.isArray(raw?.protocols) && raw?.protocols) ||
    (typeof raw?.protocols === 'string' ? String(raw?.protocols).split(',').map((s) => s.trim()) : undefined);

  const connectedAt = raw?.connectedAt ? Number(raw?.connectedAt) : undefined;

  const stats: Partial<PeerStats> = {};
  if (Number.isFinite(rtt as number)) {
    stats.lastRtt = rtt as number;
    stats.ewmaRtt = rtt as number;
    stats.samples = [rtt as number];
  }

  return {
    id: String(id ?? ''),
    address,
    agent,
    version,
    direction,
    height,
    headHash,
    protocols,
    connectedAt,
    lastSeenAt: now(),
    geo,
    stats,
  };
}

// ------------------------------- Store --------------------------------------

export const usePeersStore = create<PeersState>((set, get) => ({
  peers: {},
  order: [],
  summary: { total: 0, connected: 0, inPeers: 0, outPeers: 0 },

  rttCapacity: DEFAULT_RTT_CAP,
  ewmaAlpha: DEFAULT_ALPHA,
  staleMs: DEFAULT_STALE_MS,

  reset: () =>
    set({
      peers: {},
      order: [],
      summary: { total: 0, connected: 0, inPeers: 0, outPeers: 0 },
      rttCapacity: DEFAULT_RTT_CAP,
      ewmaAlpha: DEFAULT_ALPHA,
      staleMs: DEFAULT_STALE_MS,
    }),

  configure: (cfg: PeersConfig) =>
    set((s) => ({
      rttCapacity: cfg.rttCapacity ?? s.rttCapacity,
      ewmaAlpha: cfg.ewmaAlpha ?? s.ewmaAlpha,
      staleMs: cfg.staleMs ?? s.staleMs,
    })),

  upsertPeer: (p) =>
    set((s) => {
      const prev = s.peers[p.id];
      const capacity = s.rttCapacity;

      let stats: PeerStats;
      if (prev) {
        // merge stats
        const base = prev.stats;
        const nextSamples = base.samples.slice();
        if (Number.isFinite(p.stats?.lastRtt as number)) {
          pushRing(nextSamples, p.stats!.lastRtt as number, capacity);
        }
        const ew = Number.isFinite(p.stats?.lastRtt as number)
          ? ewma(base.ewmaRtt, p.stats!.lastRtt as number, s.ewmaAlpha)
          : base.ewmaRtt;

        const sent = base.sentPings + (p.stats?.sentPings ?? 0);
        const recv = base.recvPongs + (p.stats?.recvPongs ?? (Number.isFinite(p.stats?.lastRtt as number) ? 1 : 0));
        const loss = sent > 0 ? Math.max(0, Math.min(1, (sent - recv) / sent)) : 0;

        stats = {
          capacity,
          samples: nextSamples,
          sentPings: sent,
          recvPongs: recv,
          lastRtt: p.stats?.lastRtt ?? base.lastRtt,
          ewmaRtt: ew,
          jitter: computeJitter(nextSamples),
          loss,
        };
      } else {
        const samples: number[] = [];
        const rtt = p.stats?.lastRtt;
        if (Number.isFinite(rtt as number)) samples.push(rtt as number);
        stats = {
          capacity,
          samples,
          sentPings: p.stats?.sentPings ?? 0,
          recvPongs: p.stats?.recvPongs ?? (Number.isFinite(rtt as number) ? 1 : 0),
          lastRtt: rtt,
          ewmaRtt: Number.isFinite(rtt as number) ? (rtt as number) : undefined,
          jitter: computeJitter(samples),
          loss: 0,
        };
      }

      const merged: Peer = {
        id: p.id,
        address: p.address ?? prev?.address,
        agent: p.agent ?? prev?.agent,
        version: p.version ?? prev?.version,
        direction: p.direction ?? prev?.direction,
        height: p.height ?? prev?.height,
        headHash: p.headHash ?? prev?.headHash,
        protocols: p.protocols ?? prev?.protocols,
        connectedAt: p.connectedAt ?? prev?.connectedAt ?? now(),
        lastSeenAt: now(),
        geo: p.geo ?? prev?.geo,
        tags: p.tags ?? prev?.tags,
        stats,
      };

      const peers = { ...s.peers, [merged.id]: merged };
      const order = sortIds(peers);
      const summary = summarize(peers);
      return { peers, order, summary };
    }),

  removePeer: (id) =>
    set((s) => {
      if (!s.peers[id]) return {};
      const peers = { ...s.peers };
      delete peers[id];
      return { peers, order: sortIds(peers), summary: summarize(peers) };
    }),

  pruneStale: () =>
    set((s) => {
      const cutoff = now() - s.staleMs;
      const peers: Record<string, Peer> = {};
      for (const [id, p] of Object.entries(s.peers)) {
        if ((p.lastSeenAt ?? 0) >= cutoff) peers[id] = p;
      }
      return { peers, order: sortIds(peers), summary: summarize(peers) };
    }),

  ingestSnapshot: (payload: any) => {
    // Accept payload.peers or payload.result.peers or payload (array)
    const arr: any[] =
      Array.isArray(payload) ? payload :
      Array.isArray(payload?.peers) ? payload.peers :
      Array.isArray(payload?.result?.peers) ? payload.result.peers :
      [];

    if (!arr || arr.length === 0) return;

    for (const raw of arr) {
      const n = extractPeerShape(raw);
      if (!n.id) continue;

      // If snapshot includes an RTT/latency, flow it through upsert
      usePeersStore.getState().upsertPeer({
        ...n,
        stats: {
          lastRtt: n.stats?.lastRtt,
          sentPings: 0,
          recvPongs: Number.isFinite(n.stats?.lastRtt as number) ? 1 : 0,
        },
      });
    }
  },

  recordPing: (id) =>
    set((s) => {
      const p = s.peers[id];
      if (!p) return {};
      const stats = { ...p.stats, sentPings: p.stats.sentPings + 1 };
      const peer = { ...p, stats };
      const peers = { ...s.peers, [id]: peer };
      return { peers, summary: summarize(peers) };
    }),

  markPong: (id, rttMs) =>
    set((s) => {
      const p = s.peers[id];
      if (!p) return {};
      const capacity = s.rttCapacity;
      const samples = p.stats.samples.slice();
      pushRing(samples, rttMs, capacity);
      const ew = ewma(p.stats.ewmaRtt, rttMs, s.ewmaAlpha);
      const recvPongs = p.stats.recvPongs + 1;
      const loss = p.stats.sentPings > 0 ? Math.max(0, Math.min(1, (p.stats.sentPings - recvPongs) / p.stats.sentPings)) : 0;
      const stats: PeerStats = {
        ...p.stats,
        samples,
        recvPongs,
        lastRtt: rttMs,
        ewmaRtt: ew,
        jitter: computeJitter(samples),
        loss,
      };
      const peer: Peer = { ...p, stats, lastSeenAt: now() };
      const peers = { ...s.peers, [id]: peer };
      return { peers, order: sortIds(peers), summary: summarize(peers) };
    }),

  markFailedPing: (id) =>
    set((s) => {
      const p = s.peers[id];
      if (!p) return {};
      const sent = p.stats.sentPings + 1;
      const loss = sent > 0 ? Math.max(0, Math.min(1, (sent - p.stats.recvPongs) / sent)) : 0;
      const stats: PeerStats = { ...p.stats, sentPings: sent, loss };
      const peer: Peer = { ...p, stats };
      const peers = { ...s.peers, [id]: peer };
      return { peers, summary: summarize(peers) };
    }),

  list: () => {
    const { peers, order } = get();
    return order.map((id) => peers[id]).filter(Boolean);
  },

  topByLatency: (limit = 10) => {
    const list = get().list();
    return list
      .filter((p) => Number.isFinite(p.stats.ewmaRtt as number))
      .slice(0, Math.max(1, limit));
  },

  byId: (id) => get().peers[id],
}));

function sortIds(peers: Record<string, Peer>): string[] {
  return Object.values(peers)
    .sort((a, b) => {
      const ar = a.stats.ewmaRtt ?? Number.POSITIVE_INFINITY;
      const br = b.stats.ewmaRtt ?? Number.POSITIVE_INFINITY;
      if (ar !== br) return ar - br; // lower latency first
      // tie-breaker: newer lastSeen first
      const at = a.lastSeenAt ?? 0;
      const bt = b.lastSeenAt ?? 0;
      return bt - at;
    })
    .map((p) => p.id);
}

function summarize(peers: Record<string, Peer>): PeersSummary {
  const vals = Object.values(peers);
  const total = vals.length;
  const connected = total;
  const inPeers = vals.filter((p) => p.direction === 'in').length;
  const outPeers = vals.filter((p) => p.direction === 'out').length;

  const rtts = vals.map((p) => p.stats.ewmaRtt!).filter(Number.isFinite) as number[];
  const avgRtt = mean(rtts);
  const medianRtt = median(rtts);

  const heights = vals.map((p) => p.height!).filter(Number.isFinite) as number[];
  const avgHeight = mean(heights);

  return { total, connected, inPeers, outPeers, avgRtt, medianRtt, avgHeight };
}

// --------------------------- Convenience Hooks ------------------------------

/**
 * Hook for simple consumer usage:
 *   const peers = usePeersList()
 */
export function usePeersList() {
  return usePeersStore((s) => s.list(), shallow);
}

/**
 * Push a generic peers payload (from RPC/WS) into the store.
 */
export function updatePeersFromSnapshot(payload: any) {
  usePeersStore.getState().ingestSnapshot(payload);
}

/**
 * Record a ping attempt; on pong, call markPong(id, rttMs).
 */
export function recordPingAttempt(id: PeerId) {
  usePeersStore.getState().recordPing(id);
}
export function recordPong(id: PeerId, rttMs: number) {
  usePeersStore.getState().markPong(id, rttMs);
}
export function recordPingTimeout(id: PeerId) {
  usePeersStore.getState().markFailedPing(id);
}
