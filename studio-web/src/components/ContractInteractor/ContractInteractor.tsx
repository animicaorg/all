import * as React from "react";
import { useCompileStore } from "../../state/compile";
import { useProjectStore } from "../../state/project";
import { useNetwork } from "../../state/network";
import { useAccount } from "../../hooks/useAccount";
import { RpcClient } from "../../services/rpc";
import { formatAddress } from "../../utils/format";
import { sha3_256Hex } from "../../utils/hash";
import { cx } from "../../utils/classnames";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import * as TxBuild from "@animica/sdk/tx/build";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import * as TxSend from "@animica/sdk/tx/send";

const RECENTS_LIMIT = 8;
const HISTORY_LIMIT = 50;

const MAINNET_CHAIN_ID = 1;

type AbiParam = {
  name?: string;
  type?: any;
  kind?: string;
  items?: any;
  components?: any[];
  fields?: any[];
  indexed?: boolean;
};

type AbiFunction = {
  name: string;
  inputs?: AbiParam[];
  outputs?: AbiParam[];
  stateMutability?: string;
  mutability?: string;
  kind?: string;
  defaults?: Record<string, unknown>;
};

type AbiEvent = {
  name: string;
  fields?: AbiParam[];
  inputs?: AbiParam[];
  topic?: string;
};

type NormalizedAbi = {
  name?: string;
  functions: AbiFunction[];
  events: AbiEvent[];
  raw?: any;
};

type ContractMeta = {
  address: string;
  label?: string;
  manifestName?: string;
  manifestVersion?: string;
  abi?: any;
  lastUsedAt: number;
};

type HistoryItem = {
  id: string;
  when: number;
  kind: "call" | "tx" | "state";
  fn?: string;
  args?: unknown[];
  status?: string;
  txHash?: string;
  receipt?: any;
  result?: unknown;
  error?: string;
};

type LogItem = {
  id: string;
  when: number;
  source: "call" | "tx" | "simulate";
  name?: string;
  txHash?: string;
  height?: number | null;
  data?: unknown;
};

export default function ContractInteractor() {
  const compile = useCompileStore((s: any) => s);
  const project = useProjectStore((s: any) => s);
  const network = useNetwork((s: any) => s.network);
  const { status: accountStatus, account, connect } = useAccount({ autoConnect: true, expectedChainId: network.chainId });

  const [address, setAddress] = React.useState<string>("");
  const [recentContracts, setRecentContracts] = React.useState<ContractMeta[]>([]);
  const [history, setHistory] = React.useState<HistoryItem[]>([]);
  const [logs, setLogs] = React.useState<LogItem[]>([]);

  const [selectedFn, setSelectedFn] = React.useState<string>("");
  const [argsByFn, setArgsByFn] = React.useState<Record<string, string[]>>({});
  const [callResult, setCallResult] = React.useState<unknown>(null);
  const [callError, setCallError] = React.useState<string | null>(null);
  const [callBusy, setCallBusy] = React.useState(false);

  const [storageKey, setStorageKey] = React.useState<string>("");
  const [storageEntries, setStorageEntries] = React.useState<{ key: string; value: unknown }[]>([]);
  const [storageBusy, setStorageBusy] = React.useState(false);
  const [storageError, setStorageError] = React.useState<string | null>(null);

  const [txBusy, setTxBusy] = React.useState(false);
  const [txError, setTxError] = React.useState<string | null>(null);
  const [txHash, setTxHash] = React.useState<string | null>(null);
  const [receipt, setReceipt] = React.useState<any | null>(null);
  const [confirmPending, setConfirmPending] = React.useState(false);

  const [gasLimit, setGasLimit] = React.useState<string>("");
  const [maxFee, setMaxFee] = React.useState<string>("");
  const [value, setValue] = React.useState<string>("0");
  const [nonce, setNonce] = React.useState<string>("");

  const [logFilterEvent, setLogFilterEvent] = React.useState<string>("");
  const [logFilterTx, setLogFilterTx] = React.useState<string>("");
  const [logFilterMinHeight, setLogFilterMinHeight] = React.useState<string>("");
  const [logFilterMaxHeight, setLogFilterMaxHeight] = React.useState<string>("");

  const projectKey = React.useMemo(() => {
    const paths = Object.keys(project?.files ?? {}).sort().join("|");
    const manifestName = String(resolveManifest(compile, project)?.name ?? "");
    return sha3_256Hex(`${manifestName}|${paths}`);
  }, [project?.files, compile?.manifest]);

  const abi = React.useMemo(() => normalizeAbi(resolveAbi(compile, project)), [compile?.manifest, compile?.abi, project?.files]);

  const functions = abi.functions;
  const currentFn = functions.find((f) => f.name === selectedFn) ?? functions[0];
  const isMainnet = network.chainId === MAINNET_CHAIN_ID || network.id === "mainnet";

  React.useEffect(() => {
    if (!selectedFn && functions.length) setSelectedFn(functions[0]?.name ?? "");
  }, [functions, selectedFn]);

  React.useEffect(() => {
    const stored = readLocal<ContractMeta[]>(recentKey(projectKey), []);
    setRecentContracts(stored);
    const storedHistory = readLocal<HistoryItem[]>(historyKey(projectKey), []);
    setHistory(storedHistory);
  }, [projectKey]);

  React.useEffect(() => {
    if (!currentFn) return;
    setArgsByFn((prev) => {
      if (prev[currentFn.name]) return prev;
      const defaults = defaultArgs(currentFn);
      return { ...prev, [currentFn.name]: defaults };
    });
  }, [currentFn]);

  const onPickRecent = (addr: string) => {
    const hit = recentContracts.find((r) => r.address === addr);
    if (!hit) return;
    setAddress(hit.address);
    setRecentContracts((prev) => {
      const next = [hit, ...prev.filter((p) => p.address !== hit.address)].slice(0, RECENTS_LIMIT);
      writeLocal(recentKey(projectKey), next);
      return next;
    });
  };

  const saveRecent = (addr: string) => {
    if (!addr) return;
    const manifest = resolveManifest(compile, project);
    const entry: ContractMeta = {
      address: addr,
      label: manifest?.name ?? "Contract",
      manifestName: manifest?.name,
      manifestVersion: manifest?.version,
      abi: manifest?.abi ?? compile?.abi ?? undefined,
      lastUsedAt: Date.now(),
    };
    setRecentContracts((prev) => {
      const next = [entry, ...prev.filter((p) => p.address !== addr)].slice(0, RECENTS_LIMIT);
      writeLocal(recentKey(projectKey), next);
      return next;
    });
  };

  const rpcClient = React.useMemo(
    () => new RpcClient({ url: network.rpcUrl, chainId: network.chainId, wsUrl: network.wsUrl }),
    [network.rpcUrl, network.chainId, network.wsUrl]
  );

  const onCall = async () => {
    setCallError(null);
    setCallResult(null);
    if (!currentFn) return;
    if (!address.trim()) {
      setCallError("Enter a contract address first.");
      return;
    }

    const parsedArgs = parseArgs(currentFn, argsByFn[currentFn.name] ?? []);

    setCallBusy(true);
    try {
      const res = await callWithFallback(rpcClient, {
        address: address.trim(),
        fn: currentFn.name,
        args: parsedArgs,
        abi: abi.raw ?? resolveAbi(compile, project),
      });

      setCallResult(res.result);
      if (res.logs?.length) {
        const next = res.logs.map((log) => ({
          id: `${Date.now()}-${Math.random()}`,
          when: Date.now(),
          source: "call",
          name: log.name,
          txHash: log.txHash,
          height: log.height ?? null,
          data: log.data,
        } as LogItem));
        setLogs((prev) => [...next, ...prev]);
      }
      pushHistory({
        id: `call-${Date.now()}`,
        when: Date.now(),
        kind: "call",
        fn: currentFn.name,
        args: parsedArgs,
        status: "ok",
        result: res.result,
      });
      saveRecent(address.trim());
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      setCallError(msg);
      pushHistory({
        id: `call-${Date.now()}`,
        when: Date.now(),
        kind: "call",
        fn: currentFn.name,
        args: parsedArgs,
        status: "error",
        error: msg,
      });
    } finally {
      setCallBusy(false);
    }
  };

  const onReadStorage = async () => {
    setStorageError(null);
    if (!address.trim()) {
      setStorageError("Enter a contract address first.");
      return;
    }
    if (!storageKey.trim()) {
      setStorageError("Enter a storage key.");
      return;
    }
    setStorageBusy(true);
    try {
      const res = await getStorageWithFallback(rpcClient, address.trim(), storageKey.trim());
      setStorageEntries((prev) => {
        const next = [{ key: storageKey.trim(), value: res }, ...prev.filter((p) => p.key !== storageKey.trim())];
        return next.slice(0, 50);
      });
      pushHistory({
        id: `state-${Date.now()}`,
        when: Date.now(),
        kind: "state",
        status: "ok",
        result: { key: storageKey.trim(), value: res },
      });
      saveRecent(address.trim());
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      setStorageError(msg);
      pushHistory({
        id: `state-${Date.now()}`,
        when: Date.now(),
        kind: "state",
        status: "error",
        error: msg,
      });
    } finally {
      setStorageBusy(false);
    }
  };

  const onSendTx = async () => {
    setTxError(null);
    setTxHash(null);
    setReceipt(null);
    if (!currentFn) return;
    if (!address.trim()) {
      setTxError("Enter a contract address first.");
      return;
    }
    if (!account?.address) {
      await connect();
      if (!account?.address) {
        setTxError("Connect a wallet to sign transactions.");
        return;
      }
    }
    if (!confirmPending) {
      setConfirmPending(true);
      return;
    }
    setConfirmPending(false);

    const parsedArgs = parseArgs(currentFn, argsByFn[currentFn.name] ?? []);

    setTxBusy(true);
    try {
      const { tx, signBytes } = await buildCallCompat({
        to: address.trim(),
        from: account?.address ?? "",
        chainId: network.chainId,
        args: parsedArgs,
        method: currentFn.name,
        gasLimit: gasLimit ? BigInt(gasLimit) : undefined,
        maxFee: maxFee ? BigInt(maxFee) : undefined,
        value: value ? BigInt(value) : undefined,
        nonce: nonce ? BigInt(nonce) : undefined,
      });

      const sig = await signCompat(signBytes);
      const sendOut = await sendSignedCompat(tx, signBytes, sig);
      const hash = sendOut.txHash || sendOut.hash || null;
      setTxHash(hash);
      saveRecent(address.trim());

      const rcpt = await awaitReceiptCompat(rpcClient, hash);
      setReceipt(rcpt);

      if (rcpt?.logs?.length) {
        const nextLogs = rcpt.logs.map((log: any) => ({
          id: `${Date.now()}-${Math.random()}`,
          when: Date.now(),
          source: "tx",
          txHash: hash ?? undefined,
          height: rcpt.blockNumber ?? rcpt.blockHeight ?? null,
          data: log,
        } as LogItem));
        setLogs((prev) => [...nextLogs, ...prev]);
      }

      pushHistory({
        id: `tx-${Date.now()}`,
        when: Date.now(),
        kind: "tx",
        fn: currentFn.name,
        args: parsedArgs,
        status: "sent",
        txHash: hash ?? undefined,
        receipt: rcpt ?? undefined,
      });
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      setTxError(msg);
      pushHistory({
        id: `tx-${Date.now()}`,
        when: Date.now(),
        kind: "tx",
        fn: currentFn.name,
        args: parsedArgs,
        status: "error",
        error: msg,
      });
    } finally {
      setTxBusy(false);
    }
  };

  const pushHistory = (item: HistoryItem) => {
    setHistory((prev) => {
      const next = [item, ...prev].slice(0, HISTORY_LIMIT);
      writeLocal(historyKey(projectKey), next);
      return next;
    });
  };

  const filteredLogs = logs.filter((l) => {
    if (logFilterEvent && l.name) {
      if (!l.name.toLowerCase().includes(logFilterEvent.toLowerCase())) return false;
    } else if (logFilterEvent && !l.name) {
      return false;
    }
    if (logFilterTx && l.txHash) {
      if (!l.txHash.toLowerCase().includes(logFilterTx.toLowerCase())) return false;
    } else if (logFilterTx && !l.txHash) {
      return false;
    }
    if (logFilterMinHeight) {
      const min = Number(logFilterMinHeight);
      if (Number.isFinite(min) && typeof l.height === "number" && l.height < min) return false;
      if (Number.isFinite(min) && l.height == null) return false;
    }
    if (logFilterMaxHeight) {
      const max = Number(logFilterMaxHeight);
      if (Number.isFinite(max) && typeof l.height === "number" && l.height > max) return false;
    }
    return true;
  });

  const readOnly = currentFn ? isReadOnly(currentFn) : false;
  const canSend = currentFn ? !readOnly : false;

  return (
    <div className="h-full flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">Contract Interactor</h1>
          <p className="text-sm text-[color:var(--muted,#6b7280)]">
            ABI-driven calls, state inspection, and transaction builder.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span
            className={cx(
              "px-2 py-1 rounded text-xs font-semibold",
              isMainnet ? "bg-rose-100 text-rose-700" : "bg-emerald-100 text-emerald-700"
            )}
          >
            {isMainnet ? "Mainnet mode" : "Dev mode"}
          </span>
          <span className="text-xs text-[color:var(--muted,#6b7280)]">
            {network.label} · Chain #{network.chainId}
          </span>
        </div>
      </div>

      {isMainnet && (
        <div className="rounded border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          You are connected to mainnet. Double-check addresses and confirm every state-changing call.
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1.1fr_1.4fr_1.2fr] gap-4">
        <section className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-4">
          <h2 className="text-sm font-semibold mb-2">Contract Metadata</h2>
          <div className="space-y-3">
            <label className="block text-xs font-semibold text-[color:var(--muted,#6b7280)]">Contract address</label>
            <input
              className="w-full rounded border border-[color:var(--divider,#e5e7eb)] px-3 py-2 text-sm"
              placeholder="anim1… or 0x…"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
            {recentContracts.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {recentContracts.map((c) => (
                  <button
                    key={c.address}
                    type="button"
                    onClick={() => onPickRecent(c.address)}
                    className="px-2 py-1 rounded border border-[color:var(--divider,#e5e7eb)] text-xs hover:bg-[color:var(--panel-bg,#f9fafb)]"
                  >
                    {c.label ?? "Contract"} · {formatAddress(c.address)}
                  </button>
                ))}
              </div>
            )}
            <div className="text-xs text-[color:var(--muted,#6b7280)]">
              ABI source: {abi.raw ? "Loaded" : "No ABI detected"}
            </div>

            <div className="rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] p-3 text-xs">
              <div className="font-semibold mb-1">Manifest summary</div>
              <div>Name: {resolveManifest(compile, project)?.name ?? "—"}</div>
              <div>Version: {resolveManifest(compile, project)?.version ?? "—"}</div>
              <div>Entry: {resolveManifest(compile, project)?.entry ?? "—"}</div>
              <div>Capabilities: {formatCaps(resolveManifest(compile, project))}</div>
            </div>
          </div>
        </section>

        <section className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-4">
          <h2 className="text-sm font-semibold mb-2">ABI-driven Calls</h2>
          <div className="space-y-3">
            <label className="block text-xs font-semibold text-[color:var(--muted,#6b7280)]">Function</label>
            <select
              className="w-full rounded border border-[color:var(--divider,#e5e7eb)] px-3 py-2 text-sm"
              value={currentFn?.name ?? ""}
              onChange={(e) => setSelectedFn(e.target.value)}
            >
              {functions.map((fn) => (
                <option key={fn.name} value={fn.name}>
                  {fn.name} · {labelMutability(fn)}
                </option>
              ))}
            </select>

            {currentFn?.inputs?.length ? (
              <div className="space-y-2">
                {currentFn.inputs.map((param, idx) => (
                  <ArgInput
                    key={`${currentFn.name}-${idx}`}
                    param={param}
                    value={(argsByFn[currentFn.name] ?? [])[idx] ?? ""}
                    onChange={(val) =>
                      setArgsByFn((prev) => ({
                        ...prev,
                        [currentFn.name]: updateArg(prev[currentFn.name] ?? [], idx, val),
                      }))
                    }
                  />
                ))}
              </div>
            ) : (
              <div className="text-xs text-[color:var(--muted,#6b7280)]">No parameters.</div>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className={cx(
                  "px-3 py-2 rounded text-sm font-semibold",
                  callBusy
                    ? "bg-[color:var(--btn-disabled,#e5e7eb)] text-[color:var(--muted,#6b7280)]"
                    : "bg-[color:var(--accent,#0284c7)] text-white"
                )}
                disabled={callBusy}
                onClick={onCall}
              >
                {callBusy ? "Calling…" : "Call (read)"}
              </button>
              <button
                type="button"
                className={cx(
                  "px-3 py-2 rounded text-sm font-semibold",
                  !canSend
                    ? "bg-[color:var(--btn-disabled,#e5e7eb)] text-[color:var(--muted,#6b7280)]"
                    : "bg-amber-500 text-white"
                )}
                disabled={!canSend || txBusy}
                onClick={onSendTx}
              >
                {txBusy ? "Sending…" : confirmPending ? "Confirm send" : "Send transaction"}
              </button>
              {confirmPending && (
                <button
                  type="button"
                  className="px-3 py-2 rounded text-sm font-semibold border border-[color:var(--divider,#e5e7eb)]"
                  onClick={() => setConfirmPending(false)}
                >
                  Cancel
                </button>
              )}
            </div>

            {callError && (
              <div className="rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-800">
                {callError}
              </div>
            )}
            {callResult !== null && (
              <pre className="rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] p-2 text-xs whitespace-pre-wrap">
                {safeStringify(callResult, 2)}
              </pre>
            )}
          </div>

          <div className="mt-4 border-t border-[color:var(--divider,#e5e7eb)] pt-4">
            <h3 className="text-sm font-semibold mb-2">Transaction builder</h3>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs font-semibold text-[color:var(--muted,#6b7280)]">
                Gas limit
                <input
                  className="mt-1 w-full rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-sm"
                  value={gasLimit}
                  onChange={(e) => setGasLimit(e.target.value)}
                  placeholder="e.g. 120000"
                />
              </label>
              <label className="text-xs font-semibold text-[color:var(--muted,#6b7280)]">
                Max fee
                <input
                  className="mt-1 w-full rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-sm"
                  value={maxFee}
                  onChange={(e) => setMaxFee(e.target.value)}
                  placeholder="e.g. 2000000000"
                />
              </label>
              <label className="text-xs font-semibold text-[color:var(--muted,#6b7280)]">
                Value
                <input
                  className="mt-1 w-full rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-sm"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </label>
              <label className="text-xs font-semibold text-[color:var(--muted,#6b7280)]">
                Nonce
                <input
                  className="mt-1 w-full rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-sm"
                  value={nonce}
                  onChange={(e) => setNonce(e.target.value)}
                  placeholder="Optional"
                />
              </label>
            </div>
            <div className="mt-2 text-xs text-[color:var(--muted,#6b7280)]">
              Wallet status: {accountStatus} {account?.address ? `· ${formatAddress(account.address)}` : ""}
            </div>
            {txError && (
              <div className="mt-2 rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-800">
                {txError}
              </div>
            )}
            {txHash && (
              <div className="mt-2 rounded border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-800">
                Tx sent: {txHash}
              </div>
            )}
            {receipt && (
              <pre className="mt-2 rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] p-2 text-xs whitespace-pre-wrap">
                {safeStringify(receipt, 2)}
              </pre>
            )}
          </div>
        </section>

        <section className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-4">
          <h2 className="text-sm font-semibold mb-2">State Inspector</h2>
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-semibold text-[color:var(--muted,#6b7280)]">Read storage key</label>
              <div className="flex gap-2 mt-1">
                <input
                  className="flex-1 rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-sm"
                  value={storageKey}
                  onChange={(e) => setStorageKey(e.target.value)}
                  placeholder="0x… or key name"
                />
                <button
                  type="button"
                  className={cx(
                    "px-3 py-1 rounded text-sm font-semibold",
                    storageBusy
                      ? "bg-[color:var(--btn-disabled,#e5e7eb)] text-[color:var(--muted,#6b7280)]"
                      : "bg-[color:var(--accent,#0284c7)] text-white"
                  )}
                  onClick={onReadStorage}
                  disabled={storageBusy}
                >
                  {storageBusy ? "Reading…" : "Read"}
                </button>
              </div>
              {storageError && (
                <div className="mt-2 rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-800">
                  {storageError}
                </div>
              )}
            </div>

            <div>
              <div className="text-xs font-semibold text-[color:var(--muted,#6b7280)] mb-1">Storage entries</div>
              {storageEntries.length ? (
                <div className="space-y-2 max-h-[220px] overflow-auto">
                  {storageEntries.map((entry) => (
                    <div key={entry.key} className="rounded border border-[color:var(--divider,#e5e7eb)] p-2 text-xs">
                      <div className="font-semibold">{entry.key}</div>
                      <pre className="whitespace-pre-wrap break-words">{safeStringify(entry.value, 2)}</pre>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-[color:var(--muted,#6b7280)]">No storage reads yet.</div>
              )}
            </div>

            <div className="border-t border-[color:var(--divider,#e5e7eb)] pt-3">
              <h3 className="text-sm font-semibold mb-2">Logs / Events</h3>
              <div className="grid grid-cols-2 gap-2">
                <input
                  className="rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-xs"
                  placeholder="Filter event"
                  value={logFilterEvent}
                  onChange={(e) => setLogFilterEvent(e.target.value)}
                />
                <input
                  className="rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-xs"
                  placeholder="Filter tx hash"
                  value={logFilterTx}
                  onChange={(e) => setLogFilterTx(e.target.value)}
                />
                <input
                  className="rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-xs"
                  placeholder="Min height"
                  value={logFilterMinHeight}
                  onChange={(e) => setLogFilterMinHeight(e.target.value)}
                />
                <input
                  className="rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-xs"
                  placeholder="Max height"
                  value={logFilterMaxHeight}
                  onChange={(e) => setLogFilterMaxHeight(e.target.value)}
                />
              </div>
              <div className="mt-2 max-h-[220px] overflow-auto space-y-2">
                {filteredLogs.length ? (
                  filteredLogs.map((log) => (
                    <div key={log.id} className="rounded border border-[color:var(--divider,#e5e7eb)] p-2 text-xs">
                      <div className="flex justify-between">
                        <span className="font-semibold">{log.name ?? "(log)"}</span>
                        <span className="text-[color:var(--muted,#6b7280)]">{new Date(log.when).toLocaleTimeString()}</span>
                      </div>
                      {log.txHash && <div>Tx: {log.txHash}</div>}
                      {typeof log.height === "number" && <div>Height: {log.height}</div>}
                      <pre className="whitespace-pre-wrap break-words">{safeStringify(log.data, 2)}</pre>
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-[color:var(--muted,#6b7280)]">No logs captured yet.</div>
                )}
              </div>
            </div>
          </div>
        </section>
      </div>

      <section className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-4">
        <h2 className="text-sm font-semibold mb-2">Interaction History</h2>
        {history.length ? (
          <div className="space-y-2">
            {history.map((item) => (
              <div key={item.id} className="rounded border border-[color:var(--divider,#e5e7eb)] p-2 text-xs">
                <div className="flex justify-between">
                  <span className="font-semibold">
                    {item.kind.toUpperCase()} {item.fn ? `· ${item.fn}` : ""}
                  </span>
                  <span className="text-[color:var(--muted,#6b7280)]">{new Date(item.when).toLocaleTimeString()}</span>
                </div>
                {item.txHash && <div>Tx: {item.txHash}</div>}
                {item.status && <div>Status: {item.status}</div>}
                {item.error && <div className="text-rose-700">Error: {item.error}</div>}
                {item.result && <pre className="whitespace-pre-wrap">{safeStringify(item.result, 2)}</pre>}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-[color:var(--muted,#6b7280)]">No interactions recorded yet.</div>
        )}
      </section>
    </div>
  );
}

function ArgInput({ param, value, onChange }: { param: AbiParam; value: string; onChange: (v: string) => void }) {
  const label = param?.name ?? "param";
  const typeLabel = typeLabelFor(param);
  const placeholder = placeholderFor(param);

  return (
    <label className="block">
      <span className="text-xs font-semibold text-[color:var(--muted,#6b7280)]">{label}</span>
      <input
        className="mt-1 w-full rounded border border-[color:var(--divider,#e5e7eb)] px-2 py-1 text-sm"
        placeholder={typeLabel ? `${typeLabel}${placeholder ? ` · ${placeholder}` : ""}` : placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function resolveManifest(compile: any, project: any): any | null {
  if (compile?.manifest && typeof compile.manifest === "object") return compile.manifest;
  const raw = tryPickManifestJson(project);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function resolveAbi(compile: any, project: any): any | null {
  const manifest = resolveManifest(compile, project);
  return manifest?.abi ?? compile?.abi ?? null;
}

function normalizeAbi(raw: any | null): NormalizedAbi {
  if (!raw) return { functions: [], events: [] };

  if (Array.isArray(raw)) {
    const fns = raw.filter((e) => e?.type === "function" || e?.kind === "function" || e?.type === "call");
    const evs = raw.filter((e) => e?.type === "event" || e?.kind === "event");
    return {
      functions: fns.map((f) => normalizeFunctionEntry(f)),
      events: evs.map((e) => normalizeEventEntry(e)),
      raw,
    };
  }

  const functions = Array.isArray(raw.functions) ? raw.functions : [];
  const events = Array.isArray(raw.events) ? raw.events : [];
  return {
    name: raw?.contract?.name ?? raw?.name,
    functions: functions.map((f: any) => normalizeFunctionEntry(f)),
    events: events.map((e: any) => normalizeEventEntry(e)),
    raw,
  };
}

function normalizeFunctionEntry(f: any): AbiFunction {
  const inputs = Array.isArray(f?.inputs) ? f.inputs : Array.isArray(f?.params) ? f.params : [];
  const outputs = Array.isArray(f?.outputs) ? f.outputs : [];
  return {
    name: String(f?.name ?? f?.id ?? "function"),
    inputs,
    outputs,
    stateMutability: f?.stateMutability ?? f?.mutability ?? f?.state ?? f?.kind,
    defaults: f?.defaults ?? {},
  };
}

function normalizeEventEntry(e: any): AbiEvent {
  const fields = Array.isArray(e?.fields) ? e.fields : Array.isArray(e?.inputs) ? e.inputs : [];
  return {
    name: String(e?.name ?? "event"),
    fields,
    inputs: fields,
    topic: e?.topic,
  };
}

function isReadOnly(fn: AbiFunction): boolean {
  const m = String(fn.stateMutability ?? fn.mutability ?? "").toLowerCase();
  return ["view", "pure", "readonly", "read"].includes(m);
}

function labelMutability(fn: AbiFunction): string {
  const m = String(fn.stateMutability ?? fn.mutability ?? "").toLowerCase();
  if (m === "view" || m === "pure" || m === "readonly" || m === "read") return "read";
  if (m === "payable") return "payable";
  if (m === "write") return "write";
  if (m === "nonpayable") return "write";
  return m || "call";
}

function defaultArgs(fn: AbiFunction): string[] {
  return (fn.inputs ?? []).map((inp, idx) => {
    const defaults = fn.defaults ?? {};
    if (inp?.name && defaults[inp.name] !== undefined) return String(defaults[inp.name]);
    return defaultForType(inp) ?? "";
  });
}

function defaultForType(param?: AbiParam): string | null {
  const t = typeName(param);
  if (!t) return null;
  if (t.startsWith("bool")) return "false";
  if (t.startsWith("u") || t.startsWith("i") || t === "int" || t === "uint") return "0";
  if (t.startsWith("bytes")) return "0x";
  if (t === "string") return "";
  if (t === "address") return "anim1";
  if (t.endsWith("[]") || t.includes("array")) return "[]";
  return "";
}

function updateArg(prev: string[], idx: number, value: string): string[] {
  const next = prev.slice();
  next[idx] = value;
  return next;
}

function typeName(param?: AbiParam): string {
  if (!param) return "";
  if (typeof param.type === "string") return param.type;
  if (param.type?.name) return String(param.type.name);
  if (param.kind && typeof param.kind === "string") return param.kind;
  if (param.items) return "array";
  return "";
}

function typeLabelFor(param?: AbiParam): string {
  const t = typeName(param);
  if (!t) return "";
  return t;
}

function placeholderFor(param?: AbiParam): string {
  const t = typeName(param);
  if (!t) return "";
  if (t.includes("[]") || t === "array") return "JSON array";
  if (t.startsWith("bytes")) return "0x…";
  if (t === "address") return "anim1…";
  return "";
}

function parseArgs(fn: AbiFunction, rawArgs: string[]): unknown[] {
  return (fn.inputs ?? []).map((param, idx) => parseValue(param, rawArgs[idx] ?? ""));
}

function parseValue(param: AbiParam, raw: string): unknown {
  const t = typeName(param).toLowerCase();
  if (t.includes("[]") || t === "array" || param?.type?.kind === "array") {
    const inner = param?.type?.items ? ({ type: param.type.items } as AbiParam) : { type: t.replace(/\[\]$/, "") };
    const parsed = safeJsonArray(raw);
    return parsed.map((item) => parseValue(inner, String(item)));
  }
  if (t === "bool" || t === "boolean") return parseBool(raw);
  if (t === "string") return raw;
  if (t === "address") return raw.trim();
  if (t.startsWith("bytes")) return raw.trim();
  if (t.startsWith("u") || t.startsWith("i") || t === "int" || t === "uint") return parseNumberLike(raw);
  return raw;
}

function parseBool(raw: string): boolean {
  const v = raw.trim().toLowerCase();
  return v === "true" || v === "1" || v === "yes";
}

function parseNumberLike(raw: string): number | string {
  const v = raw.trim();
  if (!v) return 0;
  if (v.startsWith("0x") || v.startsWith("0X")) {
    try {
      return BigInt(v).toString();
    } catch {
      return v;
    }
  }
  const n = Number(v);
  if (Number.isFinite(n) && Math.abs(n) <= Number.MAX_SAFE_INTEGER) return n;
  return v;
}

function safeJsonArray(raw: string): unknown[] {
  const v = raw.trim();
  if (!v) return [];
  try {
    const parsed = JSON.parse(v);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    return v.split(",").map((s) => s.trim()).filter(Boolean);
  }
}

async function callWithFallback(
  rpc: RpcClient,
  params: { address: string; fn: string; args: unknown[]; abi?: any }
): Promise<{ method: string; result: any; logs?: LogItem[] }> {
  const payloads: Array<{ method: string; params: any }> = [
    { method: "state.call", params: [{ to: params.address, method: params.fn, args: params.args, abi: params.abi }] },
    { method: "state.call", params: [{ to: params.address, func: params.fn, args: params.args, abi: params.abi }] },
    { method: "contract.call", params: [{ to: params.address, method: params.fn, args: params.args, abi: params.abi }] },
    { method: "vm.call", params: [{ to: params.address, method: params.fn, args: params.args, abi: params.abi }] },
    { method: "contracts.call", params: [params.address, params.fn, params.args] },
    { method: "eth_call", params: [{ to: params.address, data: params.fn, args: params.args }] },
  ];

  let lastErr: any = null;
  for (const p of payloads) {
    try {
      const result = await rpc.request(p.method, p.params);
      const logs = extractLogs(result);
      return { method: p.method, result, logs };
    } catch (e: any) {
      lastErr = e;
    }
  }
  throw lastErr ?? new Error("No call RPC method succeeded.");
}

async function getStorageWithFallback(rpc: RpcClient, address: string, key: string): Promise<any> {
  const payloads: Array<{ method: string; params: any }> = [
    { method: "state.getStorage", params: [address, key] },
    { method: "state.getStorage", params: [{ address, key }] },
    { method: "contract.getState", params: [address, key] },
    { method: "contracts.getState", params: [address, key] },
    { method: "state.getState", params: [address, key] },
  ];
  let lastErr: any = null;
  for (const p of payloads) {
    try {
      return await rpc.request(p.method, p.params);
    } catch (e: any) {
      lastErr = e;
    }
  }
  throw lastErr ?? new Error("No storage RPC method succeeded.");
}

function extractLogs(result: any): LogItem[] | undefined {
  const logsRaw = result?.logs ?? result?.events ?? result?.result?.logs ?? result?.result?.events;
  if (!Array.isArray(logsRaw)) return undefined;
  return logsRaw.map((log: any) => ({
    id: `${Date.now()}-${Math.random()}`,
    when: Date.now(),
    source: "call",
    name: log?.name ?? log?.event ?? undefined,
    txHash: log?.txHash ?? undefined,
    height: log?.height ?? log?.blockNumber ?? undefined,
    data: log,
  }));
}

async function buildCallCompat(args: {
  to: string;
  from: string;
  chainId: number;
  method: string;
  args: unknown[];
  gasLimit?: bigint;
  maxFee?: bigint;
  value?: bigint;
  nonce?: bigint;
}): Promise<{ tx: any; signBytes: Uint8Array }> {
  const mod: any = TxBuild as any;
  const names = ["buildCall", "buildCallTx", "call", "callTx"];
  for (const n of names) {
    if (typeof mod[n] === "function") {
      const out = await mod[n](args);
      if (out?.signBytes) return out;
      if (out?.tx && out?.signBytes) return out;
      if (out?.tx) return { tx: out.tx, signBytes: out.signBytes ?? out.signable ?? out.message };
    }
  }
  const payload = JSON.stringify({ to: args.to, from: args.from, method: args.method, args: args.args });
  const signBytes = new TextEncoder().encode(payload);
  return {
    tx: { kind: "call", to: args.to, from: args.from, data: payload },
    signBytes,
  };
}

async function sendSignedCompat(tx: any, signBytes: Uint8Array, signature: Uint8Array): Promise<any> {
  const mod: any = TxSend as any;
  const names = ["sendSigned", "sendSignedTx", "broadcastSigned", "sendRawTransaction"];
  for (const n of names) {
    if (typeof mod[n] === "function") return mod[n]({ tx, signBytes, signature });
  }
  throw new Error("No sendSigned implementation available.");
}

async function awaitReceiptCompat(rpc: RpcClient, txHash?: string | null): Promise<any> {
  if (!txHash) return null;
  const start = Date.now();
  while (Date.now() - start < 120000) {
    try {
      const receipt = await rpc.getTransactionReceipt(txHash);
      if (receipt) return receipt;
    } catch {
      // ignore
    }
    await new Promise((res) => setTimeout(res, 1500));
  }
  return null;
}

async function signCompat(signBytes: Uint8Array): Promise<Uint8Array> {
  const provider = (window as any)?.animica;
  if (provider?.request) {
    try {
      const hex = await provider.request({ method: "animica_sign", params: ["0x" + toHex(signBytes)] });
      if (typeof hex === "string") return fromHex(hex);
    } catch {
      // ignore
    }
  }
  if (provider?.sign) {
    const sig = await provider.sign(signBytes);
    if (sig instanceof Uint8Array) return sig;
    if (typeof sig === "string") return fromHex(sig);
  }
  throw new Error("Wallet does not support signing yet.");
}

function formatCaps(manifest: any): string {
  const caps = manifest?.capabilities ?? manifest?.resources?.caps ?? manifest?.contract?.capabilities;
  if (!caps) return "—";
  if (Array.isArray(caps)) return caps.join(", ") || "—";
  return String(caps);
}

function safeStringify(v: any, space = 2): string {
  try {
    return JSON.stringify(v, null, space);
  } catch {
    return String(v);
  }
}

function readLocal<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeLocal<T>(key: string, value: T): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // ignore
  }
}

function recentKey(projectKey: string): string {
  return `studio.contracts.recents.${projectKey}`;
}

function historyKey(projectKey: string): string {
  return `studio.contracts.history.${projectKey}`;
}

function tryPickManifestJson(project: any): string | null {
  if (!project?.files) return null;
  const files = project.files;
  const candidates = [
    "manifest.json",
    "contracts/manifest.json",
    "manifest/manifest.json",
    "src/manifest.json",
  ];

  for (const c of candidates) {
    if (files[c]?.content) return files[c].content;
  }

  const keys = Object.keys(files);
  for (const k of keys) {
    if (k.endsWith("manifest.json")) {
      return files[k]?.content ?? null;
    }
  }

  return null;
}

function toHex(buf: Uint8Array): string {
  return Array.from(buf)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function fromHex(hex: string): Uint8Array {
  const cleaned = hex.startsWith("0x") ? hex.slice(2) : hex;
  const out = new Uint8Array(Math.ceil(cleaned.length / 2));
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(cleaned.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}
