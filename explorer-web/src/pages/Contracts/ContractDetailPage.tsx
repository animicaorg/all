import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useExplorerStore } from "../../state/store";
import Tag from "../../components/Tag";
import Code from "../../components/Code";
import { keccak256Hex } from "../../utils/hash";

type Hex = `0x${string}`;

type AbiParam = {
  name?: string;
  type: string;
  indexed?: boolean;
};

type AbiItem =
  | {
      type: "event";
      name: string;
      inputs: AbiParam[];
      anonymous?: boolean;
    }
  | {
      type: "function";
      name: string;
      inputs: AbiParam[];
      outputs?: AbiParam[];
      stateMutability?: "view" | "pure" | "nonpayable" | "payable";
    }
  | { type: string; [k: string]: any };

type Artifact = {
  address: Hex;
  name?: string;
  codeHash?: Hex;
  abi?: AbiItem[];
  createdAt?: number;
  sourceUrl?: string;
  [k: string]: any;
};

type LogEntry = {
  address: Hex;
  blockNumber: number;
  txHash: Hex;
  index?: number;
  topics: Hex[];
  data: Hex;
  timestamp?: number;
};

function isHex(x: any, minBytes = 0): x is Hex {
  return (
    typeof x === "string" &&
    /^0x[0-9a-fA-F]*$/.test(x) &&
    (minBytes === 0 || (x.length - 2) / 2 >= minBytes)
  );
}

function eventSignature(ev: Extract<AbiItem, { type: "event" }>): string {
  const types = (ev.inputs ?? []).map((i) => i.type).join(",");
  return `${ev.name}(${types})`;
}

function topic0ForEvent(ev: Extract<AbiItem, { type: "event" }>): Hex {
  return keccak256Hex(new TextEncoder().encode(eventSignature(ev))) as Hex;
}

export default function ContractDetailPage(): JSX.Element {
  const { address: routeAddress } = useParams<{ address: string }>();
  const address = (routeAddress ?? "").toLowerCase() as Hex;

  const store = useExplorerStore((s) => s as any);
  const explorerApi = store?.services?.explorerApi;
  const servicesApi = store?.services?.servicesApi;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>();
  const [artifact, setArtifact] = useState<Artifact | undefined>(undefined);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState<string | undefined>(undefined);

  const verified = Boolean(artifact?.codeHash);
  const eventItems = useMemo(
    () => (artifact?.abi ?? []).filter((x) => x?.type === "event") as Extract<AbiItem, { type: "event" }>[],
    [artifact?.abi]
  );
  const functionItems = useMemo(
    () => (artifact?.abi ?? []).filter((x) => x?.type === "function") as Extract<AbiItem, { type: "function" }>[],
    [artifact?.abi]
  );

  const eventTopicMap = useMemo(() => {
    const m = new Map<Hex, Extract<AbiItem, { type: "event" }>>();
    for (const ev of eventItems) {
      try {
        m.set(topic0ForEvent(ev), ev);
      } catch {
        // ignore malformed ABI items
      }
    }
    return m;
  }, [eventItems]);

  const fetchArtifact = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      // Prefer studio-services (richer artifact metadata)
      if (servicesApi?.artifacts?.byAddress) {
        const out = await servicesApi.artifacts.byAddress(address).catch(() => undefined);
        if (out && (out.address || out.abi || out.codeHash)) {
          setArtifact({
            address: (out.address ?? address) as Hex,
            name: out.name ?? out.contractName,
            codeHash: out.codeHash,
            abi: Array.isArray(out.abi) ? out.abi : undefined,
            createdAt: out.createdAt,
            sourceUrl: out.sourceUrl ?? out.repoUrl,
          });
          return;
        }
      }

      // Explorer REST fallback
      if (explorerApi?.contracts?.get) {
        const out = await explorerApi.contracts.get(address).catch(() => undefined);
        if (out) {
          setArtifact({
            address: (out.address ?? address) as Hex,
            name: out.name ?? out.contractName,
            codeHash: out.codeHash ?? out.bytecodeHash,
            abi: Array.isArray(out.abi) ? out.abi : undefined,
            createdAt: out.createdAt ?? out.timestamp,
            sourceUrl: out.sourceUrl,
          });
          return;
        }
      }

      // Nothing found, set minimal stub so page still renders
      setArtifact({ address, name: undefined, codeHash: undefined, abi: [], createdAt: undefined });
    } catch (e: any) {
      setError(e?.message || String(e));
      setArtifact(undefined);
    } finally {
      setLoading(false);
    }
  }, [address, explorerApi, servicesApi]);

  const fetchRecentLogs = useCallback(async () => {
    setLogsLoading(true);
    setLogsError(undefined);
    try {
      // Try explorer REST first: /contracts/:addr/events or /logs?address=...
      let items: any[] = [];
      if (explorerApi?.contracts?.events?.byAddress) {
        const out = await explorerApi.contracts.events.byAddress(address, { limit: 200 }).catch(() => undefined);
        if (out) items = Array.isArray(out.items) ? out.items : Array.isArray(out) ? out : [];
      } else if (explorerApi?.logs?.byAddress) {
        const out = await explorerApi.logs.byAddress(address, { limit: 200 }).catch(() => undefined);
        if (out) items = Array.isArray(out.items) ? out.items : Array.isArray(out) ? out : [];
      }

      const normalized: LogEntry[] = items
        .map((l: any) => ({
          address: (l.address ?? address)?.toLowerCase(),
          blockNumber: Number(l.blockNumber ?? l.block ?? l.height ?? 0),
          txHash: (l.txHash ?? l.transactionHash) as Hex,
          index: l.index ?? l.logIndex ?? undefined,
          topics: Array.isArray(l.topics) ? (l.topics as string[]).filter(isHex) : [],
          data: (l.data && isHex(l.data) ? l.data : "0x") as Hex,
          timestamp: typeof l.timestamp === "number" ? l.timestamp : undefined,
        }))
        .filter((l: LogEntry) => l.address?.toLowerCase() === address);

      // Sort newest first
      normalized.sort((a, b) => {
        const h = b.blockNumber - a.blockNumber;
        if (h !== 0) return h;
        return (b.index ?? 0) - (a.index ?? 0);
      });

      setLogs(normalized);
    } catch (e: any) {
      setLogsError(e?.message || String(e));
      setLogs([]);
    } finally {
      setLogsLoading(false);
    }
  }, [address, explorerApi]);

  useEffect(() => {
    if (!isHex(address, 20)) {
      setError("Invalid address.");
      setLoading(false);
      return;
    }
    fetchArtifact();
  }, [address, fetchArtifact]);

  useEffect(() => {
    fetchRecentLogs();
    // Optionally refresh on an interval
    const t = setInterval(fetchRecentLogs, 15_000);
    return () => clearInterval(t);
  }, [fetchRecentLogs]);

  const decodedLogs = useMemo(() => {
    return logs.map((l) => {
      const ev = eventTopicMap.get(l.topics?.[0] as Hex);
      return {
        ...l,
        _eventName: ev?.name,
        _eventSig: ev ? eventSignature(ev) : undefined,
      };
    });
  }, [logs, eventTopicMap]);

  return (
    <div className="page page-contract-detail">
      <header className="page-header">
        <div>
          <h1>Contract</h1>
          <p className="dim">
            <span className="mono">{address}</span>
          </p>
        </div>
        <div className="actions">
          <Link className="btn" to="/contracts">
            Back to list
          </Link>
        </div>
      </header>

      {loading ? (
        <p className="dim">Loading…</p>
      ) : error ? (
        <div className="alert warn">{error}</div>
      ) : !artifact ? (
        <div className="alert">Contract not found.</div>
      ) : (
        <>
          <section className="card">
            <div className="card-header">
              <h2 className="card-title flex items-center gap-2">
                {artifact.name ? <span>{artifact.name}</span> : <span className="dim">Unnamed</span>}
                <Tag tone={verified ? "green" : "yellow"}>{verified ? "Verified" : "Unverified"}</Tag>
              </h2>
              {artifact.createdAt ? (
                <div className="card-actions dim small">
                  Deployed at <time>{new Date(artifact.createdAt * 1000).toLocaleString()}</time>
                </div>
              ) : null}
            </div>
            <div className="card-body">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-16">
                <div>
                  <div className="label">Address</div>
                  <Code value={address} truncate />
                </div>
                <div>
                  <div className="label">Code Hash</div>
                  {artifact.codeHash ? (
                    <Code value={artifact.codeHash} truncate />
                  ) : (
                    <span className="dim">Unknown</span>
                  )}
                </div>
                {artifact.sourceUrl ? (
                  <div>
                    <div className="label">Source</div>
                    <a className="link" href={artifact.sourceUrl} target="_blank" rel="noreferrer">
                      {artifact.sourceUrl}
                    </a>
                  </div>
                ) : null}
              </div>
            </div>
          </section>

          <section className="card mt-3">
            <div className="card-header">
              <h2 className="card-title">ABI</h2>
              <div className="card-actions dim small">
                {artifact.abi?.length ? `${artifact.abi.length} entries` : "No ABI available"}
              </div>
            </div>
            <div className="card-body">
              {artifact.abi?.length ? (
                <AbiView abi={artifact.abi} />
              ) : (
                <p className="dim">This contract does not have a published ABI.</p>
              )}
            </div>
          </section>

          <section className="card mt-3">
            <div className="card-header">
              <h2 className="card-title">Recent Events</h2>
              <div className="card-actions">
                <button className="btn small" disabled={logsLoading} onClick={fetchRecentLogs}>
                  {logsLoading ? "Refreshing…" : "Refresh"}
                </button>
              </div>
            </div>
            <div className="card-body">
              {logsError && <div className="alert warn">{logsError}</div>}
              {decodedLogs.length ? (
                <EventsTable rows={decodedLogs} topicMap={eventTopicMap} />
              ) : logsLoading ? (
                <p className="dim">Loading events…</p>
              ) : (
                <p className="dim">
                  No recent events. Ensure your explorer backend exposes logs for this address.
                </p>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function AbiView({ abi }: { abi: AbiItem[] }) {
  const functions = (abi ?? []).filter((x) => x.type === "function") as Extract<AbiItem, { type: "function" }>[];
  const events = (abi ?? []).filter((x) => x.type === "event") as Extract<AbiItem, { type: "event" }>[];

  return (
    <div className="abi-view grid grid-cols-1 md:grid-cols-2 gap-16">
      <div>
        <h3 className="section-title">Functions</h3>
        {functions.length ? (
          <table className="table">
            <thead>
              <tr>
                <th align="left">Name</th>
                <th align="left">Inputs</th>
                <th align="left">Mutability</th>
              </tr>
            </thead>
            <tbody>
              {functions.map((f) => (
                <tr key={`fn-${f.name}`}>
                  <td className="mono">{f.name}</td>
                  <td>
                    {(f.inputs ?? []).length ? (
                      <ul className="comma-list">
                        {f.inputs.map((p, i) => (
                          <li key={i}>
                            <span className="mono">{p.type}</span>
                            {p.name ? <span className="dim"> {p.name}</span> : null}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </td>
                  <td>{f.stateMutability ?? "nonpayable"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="dim">No functions published.</p>
        )}
      </div>

      <div>
        <h3 className="section-title">Events</h3>
        {events.length ? (
          <table className="table">
            <thead>
              <tr>
                <th align="left">Event</th>
                <th align="left">Indexed</th>
                <th align="left">Non-Indexed</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => {
                const idx = (e.inputs ?? []).filter((p) => p.indexed);
                const non = (e.inputs ?? []).filter((p) => !p.indexed);
                return (
                  <tr key={`ev-${e.name}`}>
                    <td className="mono">{e.name}</td>
                    <td>
                      {idx.length ? (
                        <ul className="comma-list">
                          {idx.map((p, i) => (
                            <li key={i}>
                              <span className="mono">{p.type}</span>
                              {p.name ? <span className="dim"> {p.name}</span> : null}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <span className="dim">—</span>
                      )}
                    </td>
                    <td>
                      {non.length ? (
                        <ul className="comma-list">
                          {non.map((p, i) => (
                            <li key={i}>
                              <span className="mono">{p.type}</span>
                              {p.name ? <span className="dim"> {p.name}</span> : null}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <span className="dim">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="dim">No events published.</p>
        )}
      </div>
    </div>
  );
}

function EventsTable({
  rows,
  topicMap,
}: {
  rows: (LogEntry & { _eventName?: string; _eventSig?: string })[];
  topicMap: Map<Hex, Extract<AbiItem, { type: "event" }>>;
}) {
  return (
    <table className="table">
      <thead>
        <tr>
          <th align="left">Block</th>
          <th align="left">Tx</th>
          <th align="left">Event</th>
          <th align="left">Topics</th>
          <th align="left">Data</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((l, i) => {
          const name = l._eventName ?? "Unknown";
          const sig = l._eventSig;
          return (
            <tr key={`${l.txHash}-${l.index ?? i}`}>
              <td>{l.blockNumber}</td>
              <td className="mono">
                <Link to={`/tx/${l.txHash}`}>{short(l.txHash)}</Link>
              </td>
              <td>
                <div className="flex flex-col">
                  <span className="mono">{name}</span>
                  {sig ? <span className="dim small mono">{sig}</span> : null}
                </div>
              </td>
              <td>
                {l.topics?.length ? (
                  <ul className="stack small">
                    {l.topics.map((t, j) => (
                      <li key={j} className="mono">
                        {j === 0 && topicMap.get(t) ? (
                          <span title="topic0 (event id)">{t}</span>
                        ) : (
                          t
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="dim">—</span>
                )}
              </td>
              <td className="mono">{short(l.data, 10)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function short(h: string, bytes = 6) {
  if (!isHex(h)) return String(h);
  const n = Math.max(2, bytes) * 2;
  if (h.length <= 2 + n) return h;
  return `${h.slice(0, 2 + n / 2)}…${h.slice(-n / 2)}`;
}
