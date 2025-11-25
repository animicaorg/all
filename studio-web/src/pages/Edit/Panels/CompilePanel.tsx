import * as React from "react";
import { useCompileStore } from "../../../state/compile";
import { cx } from "../../../utils/classnames";

/**
 * CompilePanel
 * - Triggers compilation via studio-wasm
 * - Shows status, quick stats (gas estimate, IR size, code hash)
 * - Lets user clear diagnostics
 */
export default function CompilePanel() {
  const {
    status,
    gasEstimate,
    compile,
    clear,
  } = useCompileStore((s) => ({
    status: s.status,
    gasEstimate: s.gasEstimate,
    compile: s.compile,
    clear: s.clear,
  }));

  // best-effort optional fields; not all stores expose these — guarded reads
  const { irBytes, codeHash, lastCompiledAt } = useCompileStore((s: any) => ({
    irBytes: s.irBytes ?? (s.ir ? byteLen(s.ir) : undefined),
    codeHash: s.codeHash,
    lastCompiledAt: s.lastCompiledAt,
  }));

  const [busy, setBusy] = React.useState(false);
  const running = status === "running" || busy;

  const onCompile = async () => {
    setBusy(true);
    try {
      await compile();
    } catch (err) {
      // diagnostics area will surface details; keep UI calm
      // eslint-disable-next-line no-console
      console.error("compile failed:", err);
    } finally {
      setBusy(false);
    }
  };

  const onClear = () => {
    clear();
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 border-b border-[color:var(--divider,#e5e7eb)]">
        <div className="flex items-center gap-2 mb-2">
          <button
            type="button"
            onClick={onCompile}
            disabled={running}
            className={cx(
              "px-3 py-1.5 rounded text-sm font-medium",
              running
                ? "bg-[color:var(--btn-disabled,#e5e7eb)] text-[color:var(--muted,#6b7280)] cursor-not-allowed"
                : "bg-[color:var(--accent,#0284c7)] text-white hover:opacity-90"
            )}
            title="Compile active project with the in-browser Python VM"
          >
            {running ? "Compiling…" : "Compile"}
          </button>

          <button
            type="button"
            onClick={onClear}
            className="px-3 py-1.5 rounded text-sm font-medium bg-[color:var(--panel-bg,#f9fafb)] border border-[color:var(--divider,#e5e7eb)] hover:bg-white"
            title="Clear diagnostics shown below the editor"
          >
            Clear diagnostics
          </button>

          <StatusPill status={status} />
        </div>

        <div className="text-xs text-[color:var(--muted,#6b7280)]">
          Compile validates the active sources, emits IR, and estimates an upper gas bound for the last call.
        </div>
      </div>

      <div className="p-3">
        <div className="grid grid-cols-2 gap-3 text-sm">
          <Stat label="Gas (estimate)">
            {gasEstimate != null ? <code>{gasEstimate}</code> : <span className="text-[color:var(--muted,#6b7280)]">—</span>}
          </Stat>
          <Stat label="IR size">
            {irBytes != null ? <code>{irBytes} bytes</code> : <span className="text-[color:var(--muted,#6b7280)]">—</span>}
          </Stat>
          <Stat className="col-span-2" label="Code hash">
            {codeHash ? <code className="break-all">{codeHash}</code> : <span className="text-[color:var(--muted,#6b7280)]">—</span>}
          </Stat>
          <Stat label="Last compiled">
            {lastCompiledAt ? <time dateTime={new Date(lastCompiledAt).toISOString()}>{fmtAgo(lastCompiledAt)}</time> : <span className="text-[color:var(--muted,#6b7280)]">—</span>}
          </Stat>
        </div>

        <div className="mt-4 text-xs text-[color:var(--muted,#6b7280)] leading-relaxed">
          Tip: use the top toolbar’s <span className="font-medium">Run</span> to compile and then immediately execute your
          last simulation config. Diagnostics appear below the editor.
        </div>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    idle: { label: "Idle", cls: "bg-gray-100 text-gray-700" },
    running: { label: "Running", cls: "bg-amber-100 text-amber-800" },
    success: { label: "Success", cls: "bg-emerald-100 text-emerald-800" },
    error: { label: "Error", cls: "bg-rose-100 text-rose-800" },
  };
  const info = map[status] ?? map.idle;
  return <span className={cx("px-2 py-0.5 rounded text-xs font-medium", info.cls)}>{info.label}</span>;
}

function Stat(props: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={props.className}>
      <div className="text-[color:var(--muted,#6b7280)] text-xs">{props.label}</div>
      <div className="mt-0.5">{props.children}</div>
    </div>
  );
}

function byteLen(v: unknown): number | undefined {
  try {
    if (v == null) return undefined;
    if (typeof v === "string") return new TextEncoder().encode(v).length;
    if (v instanceof Uint8Array) return v.byteLength;
    if (Array.isArray(v)) return v.length;
    return undefined;
  } catch {
    return undefined;
  }
}

function fmtAgo(ts: number | string | Date): string {
  const d = typeof ts === "number" ? new Date(ts) : typeof ts === "string" ? new Date(ts) : ts;
  const diff = Date.now() - d.getTime();
  const sec = Math.max(1, Math.floor(diff / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}
