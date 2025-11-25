import React, { useEffect, useMemo, useState } from "react";
import NetworkSelect from "../components/NetworkSelect";
import AccountSelect from "../components/AccountSelect";

type SavedContract = {
  id: string;           // deterministic id (e.g., H(name|addr))
  name: string;
  address: string;      // bech32m anim1…
  abi: any;             // JSON ABI (Animica or Solidity-like)
  addedAt?: number;
};

type SelectedState = {
  address: string;
  chainId: number;
  networkName?: string;
};

type SimResult = {
  ok: boolean;
  gas?: number;
  return?: any;
  error?: string;
};

function callBackground<T = any>(msg: any): Promise<T> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (resp) => resolve(resp as T));
    } catch {
      resolve(undefined as unknown as T);
    }
  });
}

function classNames(...xs: Array<string | undefined | false>) {
  return xs.filter(Boolean).join(" ");
}

function parseAbi(raw: string): any | null {
  try {
    const obj = JSON.parse(raw);
    return obj;
  } catch {
    return null;
  }
}

function listFunctions(abi: any): Array<{
  name: string;
  inputs: Array<{ name?: string; type?: string }>;
  stateMutability?: string;
}> {
  if (!abi) return [];
  // Accept a few common shapes:
  // 1) Solidity-style array of entries
  // 2) Animica style { functions: [...] }
  const entries: any[] = Array.isArray(abi) ? abi : Array.isArray(abi?.functions) ? abi.functions : [];
  return entries
    .filter((e) => e && typeof e.name === "string")
    .map((e) => ({
      name: e.name,
      inputs: Array.isArray(e.inputs) ? e.inputs : [],
      stateMutability: e.stateMutability || e.mutability || (e.readonly ? "view" : undefined),
    }));
}

function isReadonly(mut?: string): boolean {
  if (!mut) return false;
  const m = String(mut).toLowerCase();
  return m === "view" || m === "pure" || m === "readonly";
}

export default function Contracts() {
  const [selectedWallet, setSelectedWallet] = useState<SelectedState | null>(null);
  const [contracts, setContracts] = useState<SavedContract[]>([]);
  const [selId, setSelId] = useState<string | null>(null);

  // Add form
  const [newName, setNewName] = useState("");
  const [newAddr, setNewAddr] = useState("");
  const [newAbiRaw, setNewAbiRaw] = useState("");

  // Call form
  const selected = useMemo(() => contracts.find((c) => c.id === selId) || null, [contracts, selId]);
  const fns = useMemo(() => listFunctions(selected?.abi), [selected?.abi]);
  const readFns = useMemo(() => fns.filter((f) => isReadonly(f.stateMutability)), [fns]);
  const [fnName, setFnName] = useState<string>("");
  const [argValues, setArgValues] = useState<string[]>([]);
  const [simLoading, setSimLoading] = useState(false);
  const [simResult, setSimResult] = useState<SimResult | null>(null);

  const refresh = async () => {
    const s = await callBackground<SelectedState>({ type: "wallet.getSelected" });
    if (s?.address) setSelectedWallet(s);

    const list = await callBackground<SavedContract[]>({ type: "contracts.list" });
    if (Array.isArray(list)) {
      setContracts(list);
      if (!selId && list.length) setSelId(list[0].id);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    // Reset call form when switching contract
    setFnName("");
    setArgValues([]);
    setSimResult(null);
  }, [selId]);

  const addDisabled = !newName.trim() || !newAddr.trim() || !parseAbi(newAbiRaw);

  const addContract = async () => {
    if (addDisabled) return;
    const abi = parseAbi(newAbiRaw)!;
    const res = await callBackground<{ ok: boolean; id?: string; error?: string }>({
      type: "contracts.add",
      name: newName.trim(),
      address: newAddr.trim(),
      abi,
    });
    if (res?.ok) {
      setNewName("");
      setNewAddr("");
      setNewAbiRaw("");
      await refresh();
      if (res.id) setSelId(res.id);
    } else {
      alert(res?.error || "Failed to add contract");
    }
  };

  const removeContract = async (id: string) => {
    const res = await callBackground<{ ok: boolean; error?: string }>({ type: "contracts.remove", id });
    if (!res?.ok) {
      alert(res?.error || "Failed to remove");
    }
    await refresh();
    if (selId === id) setSelId(null);
  };

  const onSelectFn = (name: string) => {
    setFnName(name);
    const def = readFns.find((f) => f.name === name);
    setArgValues(new Array(def?.inputs?.length || 0).fill(""));
    setSimResult(null);
  };

  const simulate = async () => {
    if (!selected || !fnName) return;
    setSimLoading(true);
    setSimResult(null);
    const argsPacked = argValues.map((v) => {
      const t = v.trim();
      if (t === "") return t;
      try {
        // Try parse JSON for non-string types, otherwise pass as string
        return JSON.parse(t);
      } catch {
        return t;
      }
    });

    const res = await callBackground<SimResult>({
      type: "contracts.simulate",
      address: selected.address,
      abi: selected.abi,
      method: fnName,
      args: argsPacked,
    });

    setSimResult(res || { ok: false, error: "No response" });
    setSimLoading(false);
  };

  const readonlyEmpty = selected && readFns.length === 0;

  return (
    <section className="ami-contracts">
      <div className="ami-section-header">
        <h2 className="ami-section-title">Contracts</h2>
      </div>

      <div className="ami-row">
        <NetworkSelect />
      </div>
      <div className="ami-row">
        <AccountSelect />
      </div>

      <div className="ami-grid-2">
        {/* Left: saved contracts list and add form */}
        <div>
          <div className="ami-card">
            <div className="ami-card-title">Saved</div>
            {contracts.length === 0 && <div className="ami-empty">No contracts yet.</div>}
            <ul className="ami-list-select">
              {contracts.map((c) => (
                <li
                  key={c.id}
                  className={classNames("ami-list-item", selId === c.id && "is-active")}
                  onClick={() => setSelId(c.id)}
                  title={c.address}
                >
                  <div className="ami-list-primary">
                    <strong>{c.name}</strong>
                    <code className="ami-code ami-ellipsis">{c.address}</code>
                  </div>
                  <div className="ami-list-actions">
                    <button
                      className="ami-btn ami-btn-icon"
                      title="Remove"
                      onClick={(e) => {
                        e.stopPropagation();
                        void removeContract(c.id);
                      }}
                    >
                      ✕
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="ami-card">
            <div className="ami-card-title">Add contract</div>
            <label className="ami-label">Name</label>
            <input
              className="ami-input"
              placeholder="Counter"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <label className="ami-label">Address</label>
            <input
              className="ami-input"
              placeholder="anim1…"
              value={newAddr}
              onChange={(e) => setNewAddr(e.target.value)}
            />
            <label className="ami-label">ABI (JSON)</label>
            <textarea
              className="ami-textarea"
              placeholder='{"functions":[{"name":"get","inputs":[],"stateMutability":"view"}]}'
              rows={6}
              value={newAbiRaw}
              onChange={(e) => setNewAbiRaw(e.target.value)}
            />
            <div className="ami-actions">
              <button className="ami-btn" disabled={addDisabled} onClick={addContract}>
                Save
              </button>
              {!parseAbi(newAbiRaw) && newAbiRaw.trim() && (
                <span className="ami-help-error">Invalid JSON</span>
              )}
            </div>
          </div>
        </div>

        {/* Right: contract read calls */}
        <div>
          <div className="ami-card">
            <div className="ami-card-title">Read methods</div>
            {!selected && <div className="ami-empty">Select a contract.</div>}
            {selected && (
              <>
                <div className="ami-kv">
                  <div className="ami-kv-label">Name</div>
                  <div className="ami-kv-value">{selected.name}</div>
                  <div className="ami-kv-label">Address</div>
                  <div className="ami-kv-value">
                    <code className="ami-code">{selected.address}</code>
                  </div>
                </div>

                {readonlyEmpty && (
                  <div className="ami-hint">
                    No read-only methods detected in ABI. Mark view/pure/readonly or use Animica ABI format.
                  </div>
                )}

                <label className="ami-label">Method</label>
                <select
                  className="ami-input"
                  value={fnName}
                  onChange={(e) => onSelectFn(e.target.value)}
                >
                  <option value="" disabled>
                    Select a method…
                  </option>
                  {readFns.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}({f.inputs.map((i) => i.type || "bytes").join(", ")})
                    </option>
                  ))}
                </select>

                {fnName && (
                  <>
                    <div className="ami-grid-2">
                      {readFns
                        .find((f) => f.name === fnName)
                        ?.inputs.map((inp, idx) => (
                          <div key={idx}>
                            <label className="ami-label">
                              {inp.name || `arg${idx}`} <span className="ami-dim">({inp.type || "bytes"})</span>
                            </label>
                            <input
                              className="ami-input"
                              placeholder='e.g., 42 or "hello" or {"x":1}'
                              value={argValues[idx] ?? ""}
                              onChange={(e) => {
                                const next = [...argValues];
                                next[idx] = e.target.value;
                                setArgValues(next);
                              }}
                            />
                          </div>
                        ))}
                    </div>

                    <div className="ami-actions">
                      <button className="ami-btn" disabled={simLoading} onClick={simulate}>
                        {simLoading ? "Simulating…" : "Simulate Read"}
                      </button>
                    </div>
                  </>
                )}

                {simResult && (
                  <div className={classNames("ami-card", "ami-card-muted")}>
                    {!simResult.ok && (
                      <div className="ami-help-error">
                        Error: <code className="ami-code">{simResult.error || "call failed"}</code>
                      </div>
                    )}
                    {simResult.ok && (
                      <>
                        <div className="ami-kv">
                          <div className="ami-kv-label">Gas (est.)</div>
                          <div className="ami-kv-value">{simResult.gas ?? "—"}</div>
                        </div>
                        <label className="ami-label">Return</label>
                        <pre className="ami-pre">
{JSON.stringify(simResult.return, null, 2)}
                        </pre>
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="ami-card ami-card-muted">
            <ul className="ami-list">
              <li>This panel only simulates <em>read-only</em> calls.</li>
              <li>For state-changing calls, use the dapp flow (Approve window opens for writes).</li>
              <li>ABI formats supported: Animica JSON and Solidity-like arrays.</li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}
