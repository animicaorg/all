import * as React from "react";
import { cx } from "../../../utils/classnames";
import { useCompileStore } from "../../../state/compile";
import { useSimulateStore } from "../../../state/simulate";

/**
 * SimulatePanel
 * - Lets you pick a function from the current ABI
 * - Edit arguments with light type hints
 * - Run a local simulation (no state write) via studio-wasm
 * - Shows return value and emitted events/logs
 *
 * This component is defensive about store shapes; unknown fields are optional.
 */

type AbiParam = {
  name?: string;
  type?: string; // "uint256", "bytes", "bool", "address", etc.
};

type AbiFunction = {
  type?: string; // "function"
  name?: string;
  inputs?: AbiParam[];
  stateMutability?: string;
};

export default function SimulatePanel() {
  // Pull ABI (wherever the compile store keeps it)
  const { abi } = useCompileStore((s: any) => ({
    abi: s.abi ?? s.manifest?.abi ?? s.lastAbi ?? [],
  }));

  const functions: AbiFunction[] = Array.isArray(abi)
    ? (abi as AbiFunction[]).filter((f) => (f.type ?? "function") === "function")
    : [];

  // Simulate store contract
  const sim = useSimulateStore((s: any) => s);
  const status: string = sim?.status ?? "idle";
  const running = status === "running" || status === "pending";
  const result = sim?.result;
  const logs: any[] = sim?.logs ?? [];
  const error: unknown = sim?.error;

  // local selection
  const [fnName, setFnName] = React.useState<string>(() => functions[0]?.name ?? "");
  const currentFn = React.useMemo(
    () => functions.find((f) => f.name === fnName) ?? functions[0],
    [functions, fnName]
  );

  const [args, setArgs] = React.useState<any[]>(() => defaultArgs(currentFn));

  // Keep args in sync with function selection
  React.useEffect(() => {
    setArgs(defaultArgs(currentFn));
    // propagate to store if it supports it
    sim?.setSelection?.({ fn: currentFn?.name, args: defaultArgs(currentFn) });
  }, [currentFn?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const onChangeArg = (idx: number, v: string) => {
    setArgs((prev) => {
      const next = prev.slice();
      next[idx] = v;
      return next;
    });
  };

  const onPickFn = (name: string) => {
    setFnName(name);
  };

  const [busy, setBusy] = React.useState(false);

  const runSim = async () => {
    if (!currentFn?.name) return;
    setBusy(true);
    try {
      const cooked = coerceArgs(currentFn?.inputs ?? [], args);
      // try the simulate store APIs with some flexibility
      if (typeof sim?.run === "function") {
        await sim.run(currentFn.name, cooked);
      } else if (typeof sim?.simulate === "function") {
        await sim.simulate({ fn: currentFn.name, args: cooked });
      } else if (typeof sim?.simulateCall === "function") {
        await sim.simulateCall(currentFn.name, cooked);
      } else if (typeof sim?.exec === "function") {
        await sim.exec(currentFn.name, cooked);
      } else {
        throw new Error("No simulate method wired in useSimulateStore");
      }
    } catch (e) {
      // best-effort error surfacing
      sim?.setError?.(e);
      // eslint-disable-next-line no-console
      console.error("simulate error:", e);
    } finally {
      setBusy(false);
    }
  };

  const clear = () => {
    sim?.clear?.();
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 border-b border-[color:var(--divider,#e5e7eb)]">
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="px-2 py-1.5 rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] text-sm"
            value={currentFn?.name ?? ""}
            onChange={(e) => onPickFn(e.target.value)}
            title="Select a function from the ABI"
          >
            {functions.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}
              </option>
            ))}
          </select>

          <button
            type="button"
            onClick={runSim}
            disabled={running || busy || !currentFn?.name}
            className={cx(
              "px-3 py-1.5 rounded text-sm font-medium",
              running || busy
                ? "bg-[color:var(--btn-disabled,#e5e7eb)] text-[color:var(--muted,#6b7280)] cursor-not-allowed"
                : "bg-[color:var(--accent,#0284c7)] text-white hover:opacity-90"
            )}
            title="Run a local simulation (no state is written)"
          >
            {running || busy ? "Running…" : "Run"}
          </button>

          <button
            type="button"
            onClick={clear}
            className="px-3 py-1.5 rounded text-sm font-medium bg-[color:var(--panel-bg,#f9fafb)] border border-[color:var(--divider,#e5e7eb)] hover:bg-white"
            title="Clear last result & logs"
          >
            Clear
          </button>

          <StatusPill status={status} />
        </div>
      </div>

      {/* Args editor */}
      <div className="p-3 border-b border-[color:var(--divider,#e5e7eb)]">
        {currentFn?.inputs?.length ? (
          <div className="space-y-2">
            {currentFn.inputs.map((inp, i) => (
              <ArgRow
                key={`${currentFn.name}-arg-${i}`}
                index={i}
                param={inp}
                value={args[i]}
                onChange={(v) => onChangeArg(i, v)}
              />
            ))}
          </div>
        ) : (
          <div className="text-sm text-[color:var(--muted,#6b7280)]">This function takes no arguments.</div>
        )}
      </div>

      {/* Output */}
      <div className="p-3 grid grid-cols-1 gap-3 overflow-auto">
        <section>
          <h3 className="text-sm font-semibold mb-1">Return</h3>
          <div className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-2 overflow-auto text-xs">
            {result !== undefined ? (
              <pre className="whitespace-pre-wrap break-words">
                {safeStringify(result, 2)}
              </pre>
            ) : (
              <span className="text-[color:var(--muted,#6b7280)]">—</span>
            )}
          </div>
        </section>

        <section>
          <h3 className="text-sm font-semibold mb-1">Events / Logs</h3>
          <div className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-2 overflow-auto text-xs">
            {logs?.length ? (
              <ul className="space-y-2">
                {logs.map((ev, idx) => (
                  <li key={idx} className="rounded border border-[color:var(--divider,#e5e7eb)] p-2">
                    <pre className="whitespace-pre-wrap break-words">{safeStringify(ev, 2)}</pre>
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-[color:var(--muted,#6b7280)]">—</span>
            )}
          </div>
        </section>

        {error && (
          <section>
            <h3 className="text-sm font-semibold mb-1">Error</h3>
            <div className="rounded border border-rose-200 bg-rose-50 p-2 overflow-auto text-xs text-rose-800">
              <pre className="whitespace-pre-wrap break-words">{formatError(error)}</pre>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    idle: { label: "Idle", cls: "bg-gray-100 text-gray-700" },
    pending: { label: "Pending", cls: "bg-amber-100 text-amber-800" },
    running: { label: "Running", cls: "bg-amber-100 text-amber-800" },
    success: { label: "Success", cls: "bg-emerald-100 text-emerald-800" },
    error: { label: "Error", cls: "bg-rose-100 text-rose-800" },
  };
  const info = map[status] ?? map.idle;
  return <span className={cx("px-2 py-0.5 rounded text-xs font-medium", info.cls)}>{info.label}</span>;
}

function ArgRow({
  index,
  param,
  value,
  onChange,
}: {
  index: number;
  param: AbiParam;
  value: any;
  onChange: (v: string) => void;
}) {
  const label = param?.name ? `${param.name} (${param?.type ?? "any"})` : `arg${index} (${param?.type ?? "any"})`;
  const placeholder = hintFor(param?.type);

  return (
    <div className="grid grid-cols-5 items-center gap-2">
      <label className="col-span-2 text-sm text-[color:var(--muted,#6b7280)]">{label}</label>
      <input
        className="col-span-3 px-2 py-1.5 rounded border border-[color:var(--divider,#e5e7eb)] bg-white text-sm font-mono"
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
    </div>
  );
}

function defaultArgs(fn?: AbiFunction): any[] {
  if (!fn?.inputs?.length) return [];
  return fn.inputs.map((p) => {
    const t = (p.type ?? "").toLowerCase();
    if (t.startsWith("uint") || t.startsWith("int")) return "0";
    if (t === "bool") return "false";
    if (t.startsWith("bytes") || t === "address") return "0x";
    return "";
  });
}

function coerceArgs(params: AbiParam[], raw: any[]): any[] {
  return (params ?? []).map((p, i) => {
    const t = (p?.type ?? "").toLowerCase();
    const v = raw?.[i];
    if (t === "bool") {
      if (typeof v === "boolean") return v;
      const s = String(v).trim().toLowerCase();
      return s === "true" || s === "1";
    }
    if (t.startsWith("uint") || t.startsWith("int")) {
      if (typeof v === "number") return v;
      const s = String(v).trim();
      if (s.startsWith("0x")) return BigInt(s).toString(); // allow hex
      const n = s === "" ? "0" : s;
      // return as string to avoid JS number limits; simulators should accept bigint-like
      return /^[+-]?\d+$/.test(n) ? n : "0";
    }
    if (t === "address" || t.startsWith("bytes")) {
      const s = String(v ?? "").trim();
      return s;
    }
    // Try JSON for complex types (arrays/tuples)
    try {
      if (typeof v === "string" && v.trim().startsWith("[") || v.trim().startsWith("{")) {
        return JSON.parse(v);
      }
    } catch {
      /* ignore */
    }
    return v;
  });
}

function hintFor(t?: string): string {
  const lt = (t ?? "").toLowerCase();
  if (lt.startsWith("uint") || lt.startsWith("int")) return "decimal or 0x…";
  if (lt === "bool") return "true / false";
  if (lt === "address") return "anim1… or 0x…";
  if (lt.startsWith("bytes")) return "0x…";
  if (lt.includes("[]") || lt.includes("tuple")) return "JSON (e.g., [1,2] or {…})";
  return "value";
}

function safeStringify(v: any, spaces = 0): string {
  try {
    return JSON.stringify(v, (_k, val) => (typeof val === "bigint" ? val.toString() : val), spaces);
  } catch {
    try {
      return String(v);
    } catch {
      return "<unprintable>";
    }
  }
}

function formatError(err: unknown): string {
  if (!err) return "";
  if (typeof err === "string") return err;
  if (err instanceof Error) return `${err.name}: ${err.message}`;
  return safeStringify(err, 2);
}
