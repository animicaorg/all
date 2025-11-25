import * as React from "react";
import { cx } from "../../../utils/classnames";
import { useSimulateStore } from "../../../state/simulate";

/**
 * EventsPanel
 * Renders emitted logs/events from the local simulator.
 * - Lightweight search & name filter
 * - Copy/Download as JSON
 * - Follow tail toggle
 * - Resilient to unknown log shapes
 */

type SimLog =
  | {
      name?: string;
      event?: string;
      address?: string;
      args?: Record<string, unknown> | unknown[];
      topics?: string[];
      data?: unknown;
      timestamp?: number;
    }
  | Record<string, any>;

export default function EventsPanel() {
  const sim = useSimulateStore((s: any) => s);
  const logs: SimLog[] = sim?.logs ?? [];
  const clearAll = sim?.clearLogs ?? sim?.clear ?? (() => {});

  const [search, setSearch] = React.useState("");
  const [follow, setFollow] = React.useState(true);

  const filtered = React.useMemo(() => {
    if (!search) return logs;
    const q = search.toLowerCase();
    return logs.filter((ev) => {
      const n = getEventName(ev).toLowerCase();
      if (n.includes(q)) return true;
      try {
        return JSON.stringify(ev, (_k, v) => (typeof v === "bigint" ? v.toString() : v))
          .toLowerCase()
          .includes(q);
      } catch {
        return false;
      }
    });
  }, [logs, search]);

  const bottomRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (follow && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [filtered.length, follow]);

  const copyJson = async () => {
    try {
      const text = safeStringify(filtered, 2);
      await navigator.clipboard.writeText(text);
      sim?.toast?.("Copied events JSON to clipboard");
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("copy failed", e);
    }
  };

  const downloadJson = () => {
    try {
      const blob = new Blob([safeStringify(filtered, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "simulate_events.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("download failed", e);
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="p-3 border-b border-[color:var(--divider,#e5e7eb)]">
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="min-w-[14rem] flex-1 px-2 py-1.5 rounded border border-[color:var(--divider,#e5e7eb)] bg-white text-sm"
            placeholder="Search by event name or JSON…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            spellCheck={false}
          />
          <label className="inline-flex items-center gap-2 text-sm text-[color:var(--muted,#6b7280)]">
            <input
              type="checkbox"
              className="accent-[color:var(--accent,#0284c7)]"
              checked={follow}
              onChange={(e) => setFollow(e.target.checked)}
            />
            Follow tail
          </label>
          <button
            type="button"
            onClick={copyJson}
            className="px-3 py-1.5 rounded text-sm font-medium bg-[color:var(--panel-bg,#f9fafb)] border border-[color:var(--divider,#e5e7eb)] hover:bg-white"
            title="Copy filtered events as JSON"
          >
            Copy JSON
          </button>
          <button
            type="button"
            onClick={downloadJson}
            className="px-3 py-1.5 rounded text-sm font-medium bg-[color:var(--panel-bg,#f9fafb)] border border-[color:var(--divider,#e5e7eb)] hover:bg-white"
            title="Download filtered events as JSON"
          >
            Download
          </button>
          <button
            type="button"
            onClick={() => clearAll()}
            className="px-3 py-1.5 rounded text-sm font-medium bg-[color:var(--panel-bg,#f9fafb)] border border-[color:var(--divider,#e5e7eb)] hover:bg-white"
            title="Clear all logs"
          >
            Clear
          </button>
          <span className="ml-auto text-xs text-[color:var(--muted,#6b7280)]">
            Showing <b>{filtered.length}</b> of {logs.length}
          </span>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-auto p-3">
        {filtered.length === 0 ? (
          <div className="text-sm text-[color:var(--muted,#6b7280)]">No events yet.</div>
        ) : (
          <ul className="space-y-2">
            {filtered.map((ev, idx) => {
              const name = getEventName(ev) || `event_${idx}`;
              const address = (ev as any).address;
              const topics = (ev as any).topics as string[] | undefined;
              const args = getArgs(ev);
              const t = (ev as any).timestamp as number | undefined;

              return (
                <li
                  key={idx}
                  className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-semibold">{name}</div>
                    <div className="text-[0.7rem] text-[color:var(--muted,#6b7280)]">
                      {t ? new Date(t).toLocaleTimeString() : ""}
                    </div>
                  </div>

                  <div className="mt-1 grid gap-2">
                    {address ? (
                      <Row label="address">
                        <code className="text-xs break-all">{String(address)}</code>
                      </Row>
                    ) : null}

                    {Array.isArray(topics) && topics.length ? (
                      <Row label="topics">
                        <div className="flex flex-col gap-1">
                          {topics.map((tp, i) => (
                            <code key={i} className="text-xs break-all">
                              {tp}
                            </code>
                          ))}
                        </div>
                      </Row>
                    ) : null}

                    {args != null ? (
                      <Row label="args">
                        <pre className="text-xs whitespace-pre-wrap break-words">
                          {safeStringify(args, 2)}
                        </pre>
                      </Row>
                    ) : null}

                    {/* Fallback raw */}
                    <details className="mt-1">
                      <summary className="cursor-pointer text-xs text-[color:var(--muted,#6b7280)]">
                        raw
                      </summary>
                      <pre className="text-[0.72rem] whitespace-pre-wrap break-words mt-1">
                        {safeStringify(ev, 2)}
                      </pre>
                    </details>
                  </div>
                </li>
              );
            })}
            <div ref={bottomRef} />
          </ul>
        )}
      </div>
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-12 gap-2">
      <div className="col-span-3 sm:col-span-2 text-[0.7rem] uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
        {label}
      </div>
      <div className="col-span-9 sm:col-span-10">{children}</div>
    </div>
  );
}

function getEventName(ev: SimLog): string {
  const any = ev as any;
  return (
    String(any?.name ?? any?.event ?? any?.evt ?? any?.type ?? "") ||
    (any?.args && any?.args?.name ? String(any.args.name) : "") ||
    ""
  );
}

function getArgs(ev: SimLog): unknown {
  const any = ev as any;
  if (any?.args != null) return any.args;
  if (any?.data != null) return any.data;
  return undefined;
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
