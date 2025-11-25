import React, { useEffect, useMemo, useState } from "react";

export type AccountItem = {
  address: string; // bech32m anim1... (preferred), or hex if not yet encoded
  name?: string;   // user label
  algo?: "dilithium3" | "sphincs_shake_128s" | string;
  path?: string;   // derivation path (optional, UI hint only)
};

type Props = {
  /** Currently selected address (bech32m). If undefined, component manages own selection. */
  value?: string;
  /** Provide accounts explicitly, otherwise they'll be fetched from background. */
  accounts?: AccountItem[];
  /** Called when the selection changes. */
  onChange?: (address: string, acct: AccountItem) => void;
  /** Disable interaction. */
  disabled?: boolean;
  /** Compact presentation. */
  compact?: boolean;
  /** Show algorithm label next to account name. */
  showAlgo?: boolean;
  /** Optional: render a "no accounts" action (e.g., open onboarding). */
  onNoAccountsAction?: () => void;
  /** Optional label override */
  label?: string;
};

type BgListResp =
  | { ok: true; accounts: AccountItem[]; selected?: string; locked?: boolean }
  | { ok: false; error: string };

/** MV3 background message helper (best-effort in tests). */
async function bgListAccounts(): Promise<BgListResp | null> {
  try {
    const chromeAny = (globalThis as any).chrome;
    if (!chromeAny?.runtime?.id || !chromeAny?.runtime?.sendMessage) return null;
    return await new Promise<BgListResp>((resolve) => {
      chromeAny.runtime.sendMessage(
        { kind: "accounts:list" },
        (resp: BgListResp) => resolve(resp ?? { ok: false, error: "no response" })
      );
    });
  } catch {
    return null;
  }
}

async function bgSelectAccount(address: string) {
  try {
    const chromeAny = (globalThis as any).chrome;
    if (!chromeAny?.runtime?.id || !chromeAny?.runtime?.sendMessage) return;
    chromeAny.runtime.sendMessage({ kind: "accounts:select", address });
  } catch {
    // ignore
  }
}

export default function AccountSelect({
  value,
  accounts,
  onChange,
  disabled,
  compact,
  showAlgo,
  onNoAccountsAction,
  label = "Account",
}: Props) {
  const [bgAccounts, setBgAccounts] = useState<AccountItem[] | null>(null);
  const [selected, setSelected] = useState<string | undefined>(value);
  const [locked, setLocked] = useState<boolean>(false);

  useEffect(() => setSelected(value), [value]);

  // Hydrate from background keyring
  useEffect(() => {
    let mounted = true;
    bgListAccounts().then((resp) => {
      if (!mounted || !resp?.ok) return;
      setBgAccounts(resp.accounts);
      setLocked(!!resp.locked);
      if (typeof value === "undefined" && resp.selected) {
        setSelected(resp.selected);
      }
    });
    return () => {
      mounted = false;
    };
  }, []); // mount once

  const list = useMemo<AccountItem[]>(() => {
    if (accounts?.length) return accounts;
    if (bgAccounts?.length) return bgAccounts;
    return [];
  }, [accounts, bgAccounts]);

  const selectedAcct = useMemo(
    () => (selected ? list.find((a) => a.address === selected) : list[0]),
    [list, selected]
  );

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const addr = e.target.value;
    const acct = list.find((a) => a.address === addr) ?? { address: addr };
    setSelected(addr);
    onChange?.(addr, acct);
    void bgSelectAccount(addr);
  }

  const nothing = list.length === 0;

  return (
    <div className={["ami-field", "ami-account-select", compact ? "ami-compact" : ""].join(" ").trim()}>
      <label className="ami-label">
        {label}
        {locked ? <span className="ami-chip">Locked</span> : null}
      </label>

      {nothing ? (
        <div className="ami-empty">
          <div className="ami-empty-avatar">?</div>
          <div className="ami-empty-text">
            <div className="ami-empty-title">No accounts</div>
            <div className="ami-empty-sub">Create or import to get started.</div>
          </div>
          {onNoAccountsAction ? (
            <button className="ami-btn" onClick={onNoAccountsAction}>
              Add
            </button>
          ) : null}
        </div>
      ) : (
        <>
          <div className="ami-select-wrap">
            <select
              className="ami-select"
              value={selectedAcct?.address ?? ""}
              onChange={handleChange}
              disabled={disabled}
              aria-label="Select account"
            >
              {list.map((a) => (
                <option key={a.address} value={a.address} title={a.address}>
                  {a.name ?? shortAddr(a.address)}
                  {showAlgo && a.algo ? ` · ${algoLabel(a.algo)}` : ""}
                </option>
              ))}
            </select>
          </div>

          {selectedAcct ? (
            <div className="ami-meta">
              <div className="ami-avatar" aria-hidden>
                {avatarText(selectedAcct)}
              </div>
              <code className="ami-code" title={selectedAcct.address}>
                {shortAddr(selectedAcct.address)}
              </code>
              <button
                className="ami-copy"
                title="Copy address"
                onClick={() => copy(selectedAcct.address)}
              >
                Copy
              </button>
              {selectedAcct.algo ? (
                <span className="ami-dim ami-algo" title={selectedAcct.algo}>
                  {algoLabel(selectedAcct.algo)}
                </span>
              ) : null}
            </div>
          ) : null}
        </>
      )}

      <style>{`
        .ami-account-select .ami-label { display:block; font-size:.8rem; opacity:.7; margin-bottom:.25rem; }
        .ami-chip {
          margin-left: .4rem;
          font-size: .7rem;
          padding: .05rem .35rem;
          border-radius: 6px;
          background: color-mix(in srgb, var(--ami-border, rgba(0,0,0,.2)) 60%, transparent);
          color: var(--ami-fg, #222);
          opacity: .8;
        }
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

        .ami-meta {
          display:flex; align-items:center; gap:.5rem;
          margin-top:.5rem;
          min-height: 32px;
        }
        .ami-avatar {
          width: 24px; height: 24px; border-radius: 50%;
          background: var(--ami-primary, #4b7cff);
          color: white; display:flex; align-items:center; justify-content:center;
          font-size: .75rem; font-weight: 600;
          user-select: none;
        }
        .ami-code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
        .ami-copy {
          margin-left: auto;
          background: transparent; border: 1px solid var(--ami-border, rgba(0,0,0,.12));
          border-radius: 6px; padding: .25rem .5rem; cursor: pointer;
        }
        .ami-copy:hover { border-color: var(--ami-primary, #4b7cff); }
        .ami-dim { opacity: .65; }
        .ami-algo { margin-left: .25rem; font-size: .8rem; }

        .ami-empty {
          display:flex; align-items:center; gap:.6rem;
          padding:.6rem .75rem; border:1px dashed var(--ami-border, rgba(0,0,0,.2));
          border-radius:8px; background: color-mix(in srgb, var(--ami-surface, #fff) 70%, transparent);
        }
        .ami-empty-avatar {
          width:28px; height:28px; border-radius:50%;
          background: var(--ami-border, rgba(0,0,0,.1));
          display:flex; align-items:center; justify-content:center; font-weight:700;
          color:#666;
        }
        .ami-empty-title { font-weight: 600; }
        .ami-empty-sub { font-size:.85rem; opacity:.7; }
        .ami-btn {
          margin-left:auto; border: 1px solid var(--ami-primary, #4b7cff);
          color: var(--ami-primary, #4b7cff); padding:.35rem .6rem; border-radius:6px; background:transparent;
          cursor:pointer;
        }
        .ami-btn:hover { background: color-mix(in srgb, var(--ami-primary, #4b7cff) 12%, transparent); }
      `}</style>
    </div>
  );
}

function shortAddr(addr: string, max = 38) {
  if (!addr) return "";
  if (addr.length <= max) return addr;
  const prefix = addr.startsWith("anim1") ? 8 : 6;
  return `${addr.slice(0, prefix)}…${addr.slice(-6)}`;
}

function algoLabel(algo: string) {
  switch (algo.toLowerCase()) {
    case "dilithium3": return "Dilithium3";
    case "sphincs_shake_128s": return "SPHINCS+-SHAKE-128s";
    default: return algo;
  }
}

function avatarText(a: AccountItem) {
  // 2-letter avatar from name or address tail
  const src = a.name?.trim() || a.address;
  const letters = src.replace(/[^a-zA-Z0-9]/g, "");
  if (letters.length >= 2) return letters.slice(0, 2).toUpperCase();
  return (letters[0] ?? "?").toUpperCase();
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // ignore
  }
}
