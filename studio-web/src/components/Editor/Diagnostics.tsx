import * as React from "react";
import { cx } from "../../utils/classnames";

export type Severity = "error" | "warning" | "info";

export interface DiagnosticRange {
  startLine: number;
  startCol: number;
  endLine?: number;
  endCol?: number;
}

export interface DiagnosticItem {
  file: string;            // relative path shown in the IDE tree
  message: string;         // human-readable explanation
  severity: Severity;      // error | warning | info
  code?: string | number;  // optional rule/code from the compiler
  source?: string;         // e.g., "vm_py", "abi", "linker"
  range?: DiagnosticRange; // 1-based positions
}

/**
 * Props for Diagnostics list.
 * - items: diagnostics to display (already sorted if desired).
 * - onJump: optional callback when a user clicks a diagnostic; used to focus editor at the position.
 * - compact: smaller paddings suited for side panels.
 * - className: extra className for outer container.
 */
export interface DiagnosticsProps {
  items: DiagnosticItem[] | null | undefined;
  onJump?: (d: DiagnosticItem) => void;
  compact?: boolean;
  className?: string;
}

/** Small badge to display severity with consistent color treatment. */
function SeverityBadge({ severity }: { severity: Severity }) {
  const label = severity === "error" ? "Error" : severity === "warning" ? "Warning" : "Info";
  return (
    <span
      className={cx(
        "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium select-none",
        severity === "error" && "bg-[var(--danger-bg,#fee2e2)] text-[var(--danger-fg,#991b1b)]",
        severity === "warning" && "bg-[var(--warn-bg,#fef3c7)] text-[var(--warn-fg,#92400e)]",
        severity === "info" && "bg-[var(--info-bg,#e0f2fe)] text-[var(--info-fg,#075985)]"
      )}
      aria-label={label}
    >
      {label}
    </span>
  );
}

/** Count diagnostics per severity for a compact summary header. */
function useCounts(items: DiagnosticItem[] | null | undefined) {
  return React.useMemo(() => {
    let errors = 0, warnings = 0, infos = 0;
    for (const d of items ?? []) {
      if (d.severity === "error") errors++;
      else if (d.severity === "warning") warnings++;
      else infos++;
    }
    return { errors, warnings, infos, total: (items ?? []).length };
  }, [items]);
}

/** Copy a single diagnostic message to clipboard (best-effort). */
async function copyDiagToClipboard(d: DiagnosticItem) {
  const pos = d.range ? `:${d.range.startLine}:${d.range.startCol}` : "";
  const code = d.code != null ? ` [${d.code}]` : "";
  const src = d.source ? ` (${d.source})` : "";
  const text = `${d.severity.toUpperCase()}: ${d.file}${pos}${src}${code}\n${d.message}`;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // ignore: clipboard may be unavailable
  }
}

/** Render a human-friendly position string. */
function Position({ range }: { range?: DiagnosticRange }) {
  if (!range) return null;
  return (
    <span className="tabular-nums text-[.85em] text-[color:var(--muted-fg,#6b7280)]">
      {range.startLine}:{range.startCol}
    </span>
  );
}

/** Empty-state view. */
function EmptyView() {
  return (
    <div className="p-3 text-sm text-[color:var(--muted-fg,#6b7280)]">
      No diagnostics. You’re all set ✨
    </div>
  );
}

/**
 * Diagnostics list component.
 * Accessible, keyboard navigable, copy-to-clipboard, and editor-jump enabled.
 */
export function Diagnostics({ items, onJump, compact, className }: DiagnosticsProps) {
  const { errors, warnings, infos, total } = useCounts(items);

  if (!items || items.length === 0) {
    return (
      <div className={cx("w-full", className)}>
        <Header counts={{ errors, warnings, infos, total }} compact={!!compact} />
        <EmptyView />
      </div>
    );
  }

  return (
    <div className={cx("w-full", className)}>
      <Header counts={{ errors, warnings, infos, total }} compact={!!compact} />
      <ul
        className={cx(
          "divide-y divide-[color:var(--divider,#e5e7eb)]",
          "border border-[color:var(--divider,#e5e7eb)] rounded-md",
          compact ? "text-[13px]" : "text-sm",
        )}
        role="list"
        aria-label="Diagnostics list"
      >
        {items.map((d, idx) => (
          <li
            key={idx}
            className={cx(
              "group flex items-start gap-3 p-3 focus-within:outline-none",
              "bg-[var(--panel-bg,transparent)]"
            )}
          >
            <div className="pt-0.5">
              <SeverityBadge severity={d.severity} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className={cx(
                    "text-left font-medium hover:underline focus:underline",
                    "text-[color:var(--link-fg,#2563eb)]"
                  )}
                  title="Jump to source"
                  onClick={() => onJump?.(d)}
                >
                  {d.file}
                </button>
                <Position range={d.range} />
                {d.code != null && (
                  <span className="text-[.85em] text-[color:var(--muted-fg,#6b7280)]">[{String(d.code)}]</span>
                )}
                {d.source && (
                  <span className="text-[.85em] text-[color:var(--muted-fg,#6b7280)]">({d.source})</span>
                )}
                <button
                  type="button"
                  onClick={() => copyDiagToClipboard(d)}
                  title="Copy diagnostic"
                  className={cx(
                    "ml-auto rounded px-2 py-0.5 border",
                    "border-[color:var(--divider,#e5e7eb)]",
                    "text-[.85em] text-[color:var(--muted-fg,#6b7280)]",
                    "hover:bg-[color:var(--hover-bg,#f3f4f6)]"
                  )}
                >
                  Copy
                </button>
              </div>
              <p className="mt-1 text-[color:var(--fg,#111827)] break-words">
                {d.message}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Header with counts summary. */
function Header({
  counts,
  compact,
}: {
  counts: { errors: number; warnings: number; infos: number; total: number };
  compact: boolean;
}) {
  return (
    <div
      className={cx(
        "mb-2 flex items-center justify-between",
        compact ? "text-xs" : "text-sm"
      )}
    >
      <div className="font-semibold">
        Diagnostics
        <span className="ml-2 text-[color:var(--muted-fg,#6b7280)]">({counts.total})</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "var(--danger-dot,#ef4444)" }}
          />
          <span className="tabular-nums">{counts.errors}</span>
          <span className="sr-only">errors</span>
        </span>
        <span className="inline-flex items-center gap-1">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "var(--warn-dot,#f59e0b)" }}
          />
          <span className="tabular-nums">{counts.warnings}</span>
          <span className="sr-only">warnings</span>
        </span>
        <span className="inline-flex items-center gap-1">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "var(--info-dot,#3b82f6)" }}
          />
          <span className="tabular-nums">{counts.infos}</span>
          <span className="sr-only">infos</span>
        </span>
      </div>
    </div>
  );
}

/* ---------------------------------------------
 * Adapters / helpers
 * -------------------------------------------*/

/**
 * Convert Monaco markers to DiagnosticItem[] for display.
 * Useful when using editor.getModelMarkers in other panels.
 */
export function fromMonacoMarkers(markers: Array<{
  message: string;
  severity: number;
  startLineNumber: number;
  startColumn: number;
  endLineNumber?: number;
  endColumn?: number;
  resource?: { path?: string | null };
  code?: string | number;
  source?: string;
}>,
fallbackFile: string
): DiagnosticItem[] {
  // Monaco severity enum: 1=Hint, 2=Info, 4=Warning, 8=Error
  const toSeverity = (s: number): Severity =>
    s >= 8 ? "error" : s >= 4 ? "warning" : "info";
  return markers.map((m) => ({
    file: m.resource?.path || fallbackFile,
    message: m.message,
    severity: toSeverity(m.severity ?? 2),
    code: m.code,
    source: m.source,
    range: {
      startLine: m.startLineNumber ?? 1,
      startCol: m.startColumn ?? 1,
      endLine: m.endLineNumber,
      endCol: m.endColumn,
    },
  }));
}

/**
 * Merge diagnostics from multiple files/sources and sort them deterministically:
 *  - errors first, then warnings, then infos
 *  - by file path asc, then by start position.
 */
export function sortDiagnostics(items: DiagnosticItem[]): DiagnosticItem[] {
  const rank = (s: Severity) => (s === "error" ? 0 : s === "warning" ? 1 : 2);
  return [...items].sort((a, b) => {
    const r = rank(a.severity) - rank(b.severity);
    if (r !== 0) return r;
    if (a.file !== b.file) return a.file.localeCompare(b.file);
    const al = a.range?.startLine ?? 0;
    const bl = b.range?.startLine ?? 0;
    if (al !== bl) return al - bl;
    const ac = a.range?.startCol ?? 0;
    const bc = b.range?.startCol ?? 0;
    return ac - bc;
  });
}

export default Diagnostics;
