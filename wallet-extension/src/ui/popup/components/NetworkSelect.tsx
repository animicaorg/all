import React, { useEffect, useMemo, useState } from "react";

export type NetworkItem = {
  chainId: number;
  name: string;
  rpcUrl?: string;
  key?: string; // optional stable id (e.g., "animica-mainnet")
};

type Props = {
  /** Currently selected chainId */
  value?: number;
  /** Optional networks list; falls back to a sane default */
  networks?: NetworkItem[];
  /** Called when user selects a different network (preferred control path) */
  onChange?: (chainId: number, net: NetworkItem) => void;
  /** Disable interaction */
  disabled?: boolean;
  /** Compact layout (smaller padding/text) */
  compact?: boolean;
  /** Show chainId next to name */
  showIds?: boolean;
};

/** Local fallback list; background may return a richer list */
const FALLBACK_NETWORKS: NetworkItem[] = [
  {
    chainId: 1337,
    name: "Animica Devnet",
    rpcUrl: "http://localhost:8545/rpc", // devnet JSON-RPC handler listens on /rpc
    key: "animica-devnet",
  },
  { chainId: 2, name: "Animica Testnet", rpcUrl: "https://rpc.testnet.animica.org", key: "animica-testnet" },
  { chainId: 1, name: "Animica Mainnet", rpcUrl: "https://rpc.animica.org", key: "animica-mainnet" },
];

type BgListResp =
  | { ok: true; networks: NetworkItem[]; selected?: number }
  | { ok: false; error: string };

async function queryBackgroundNetworks(): Promise<BgListResp | null> {
  try {
    // Try MV3-friendly messaging to background; tolerate failure in content preview/unit tests
    const chromeAny = (globalThis as any).chrome;
    if (!chromeAny?.runtime?.id || !chromeAny?.runtime?.sendMessage) return null;
    return await new Promise<BgListResp>((resolve) => {
      chromeAny.runtime.sendMessage(
        { kind: "networks:list" },
        (resp: BgListResp) => resolve(resp ?? { ok: false, error: "no response" })
      );
    });
  } catch {
    return null;
  }
}

async function tellBackgroundSelect(chainId: number) {
  try {
    const chromeAny = (globalThis as any).chrome;
    if (!chromeAny?.runtime?.id || !chromeAny?.runtime?.sendMessage) return;
    chromeAny.runtime.sendMessage({ kind: "networks:select", chainId });
  } catch {
    // ignore
  }
}

export default function NetworkSelect({
  value,
  networks,
  onChange,
  disabled,
  compact,
  showIds,
}: Props) {
  const [bgNetworks, setBgNetworks] = useState<NetworkItem[] | null>(null);
  const [selected, setSelected] = useState<number | undefined>(value);

  // Sync prop -> local state
  useEffect(() => setSelected(value), [value]);

  // Attempt to hydrate from background on mount
  useEffect(() => {
    let mounted = true;
    queryBackgroundNetworks().then((resp) => {
      if (!mounted || !resp?.ok) return;
      setBgNetworks(resp.networks);
      if (typeof value === "undefined" && typeof resp.selected === "number") {
        setSelected(resp.selected);
      }
    });
    return () => {
      mounted = false;
    };
  }, []); // mount once

  const list = useMemo<NetworkItem[]>(() => {
    if (networks?.length) return networks;
    if (bgNetworks?.length) return bgNetworks;
    return FALLBACK_NETWORKS;
  }, [networks, bgNetworks]);

  const selectedNet = useMemo(
    () => list.find((n) => n.chainId === selected) ?? list[0],
    [list, selected]
  );

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const cid = Number(e.target.value);
    const net = list.find((n) => n.chainId === cid) ?? { chainId: cid, name: `Chain ${cid}` };
    setSelected(cid);
    // Preferred: let parent manage and persist selection
    onChange?.(cid, net);
    // Also notify background in case parent didn't wire it; best-effort
    void tellBackgroundSelect(cid);
  }

  return (
    <div className={["ami-field", "ami-network-select", compact ? "ami-compact" : ""].join(" ").trim()}>
      <label className="ami-label">Network</label>
      <div className="ami-select-wrap">
        <select
          className="ami-select"
          onChange={handleChange}
          value={selectedNet?.chainId ?? ""}
          disabled={disabled}
          aria-label="Select network"
        >
          {list.map((n) => (
            <option key={n.key ?? n.chainId} value={n.chainId}>
              {n.name}
              {showIds ? ` (${n.chainId})` : ""}
            </option>
          ))}
        </select>
      </div>

      <style>{`
        .ami-network-select .ami-label { display:block; font-size:.8rem; opacity:.7; margin-bottom:.25rem; }
        .ami-select-wrap { position:relative; }
        .ami-select {
          width: 100%;
          appearance: none;
          background: var(--ami-surface, #fff);
          border: 1px solid var(--ami-border, rgba(0,0,0,.12));
          border-radius: 8px;
          padding: .5rem 2rem .5rem .75rem;
          font-size: .95rem;
          line-height: 1.2;
        }
        .ami-select:focus { outline: none; border-color: var(--ami-primary, #4b7cff); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ami-primary, #4b7cff) 20%, transparent); }
        .ami-select-wrap::after {
          content: "▾";
          position: absolute;
          right: .6rem;
          top: 50%;
          transform: translateY(-50%);
          pointer-events: none;
          opacity: .6;
        }
        .ami-compact .ami-select { padding: .4rem 1.8rem .4rem .6rem; font-size: .9rem; }
      `}</style>

      {selectedNet?.rpcUrl ? (
        <div className="ami-hint" title={selectedNet.rpcUrl}>
          <span className="ami-dim">RPC:</span> <code className="ami-code">{shortenUrl(selectedNet.rpcUrl)}</code>
        </div>
      ) : null}
    </div>
  );
}

function shortenUrl(u: string, max = 38) {
  if (u.length <= max) return u;
  const half = Math.floor((max - 1) / 2);
  return `${u.slice(0, half)}…${u.slice(-half)}`;
}
