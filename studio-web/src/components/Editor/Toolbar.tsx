import * as React from "react";
import { cx } from "../../utils/classnames";

/** Small, dependency-free SVG icons */
const IconPlay = (props: React.SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" {...props}>
    <path d="M6 4.75v10.5c0 .6.64.98 1.16.68l8.5-5.25a.8.8 0 0 0 0-1.36l-8.5-5.25A.78.78 0 0 0 6 4.75z"/>
  </svg>
);
const IconStop = (props: React.SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" {...props}>
    <rect x="5" y="5" width="10" height="10" rx="1.5"/>
  </svg>
);
const IconSave = (props: React.SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" {...props}>
    <path d="M4 5a2 2 0 0 1 2-2h6.59c.53 0 1.04.21 1.41.59l1.41 1.41c.38.37.59.88.59 1.41V15a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5zm9 1H7v3h6V6z"/>
  </svg>
);
const IconFormat = (props: React.SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" {...props}>
    <path d="M3 5.25A.75.75 0 0 1 3.75 4h12.5a.75.75 0 0 1 0 1.5H3.75A.75.75 0 0 1 3 5.25zM3 10a.75.75 0 0 1 .75-.75h7.5a.75.75 0 0 1 0 1.5h-7.5A.75.75 0 0 1 3 10zm0 4.75a.75.75 0 0 1 .75-.75h12.5a.75.75 0 0 1 0 1.5H3.75a.75.75 0 0 1-.75-.75z"/>
  </svg>
);

export interface ToolbarProps {
  onRun: () => void | Promise<void>;
  /** Optional stop handler; if provided and isRunning=true, Stop replaces Run */
  onStop?: () => void | Promise<void>;
  onSave: () => void | Promise<void>;
  onFormat: () => void | Promise<void>;
  /** UI state flags */
  isRunning?: boolean;
  isDirty?: boolean;
  canRun?: boolean;
  canSave?: boolean;
  canFormat?: boolean;
  /** Enable default hotkeys: ⌘/Ctrl+Enter (Run), ⌘/Ctrl+S (Save), Shift+Alt+F (Format) */
  hotkeys?: boolean;
  className?: string;
  /** Optional right-side slot (e.g., selector, network, gas info) */
  rightSlot?: React.ReactNode;
}

/**
 * Editor toolbar with Run / Save / Format actions.
 * Accessible, keyboard-friendly, and themable via CSS variables.
 */
export function Toolbar({
  onRun,
  onStop,
  onSave,
  onFormat,
  isRunning = false,
  isDirty = false,
  canRun = true,
  canSave = true,
  canFormat = true,
  hotkeys = true,
  className,
  rightSlot,
}: ToolbarProps) {
  const runDisabled = isRunning ? false : !canRun;
  const saveDisabled = !canSave || (!isDirty && canSave !== false); // allow save even when not dirty if explicitly enabled
  const formatDisabled = !canFormat;

  // Hotkeys
  React.useEffect(() => {
    if (!hotkeys) return;
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      // Run: Cmd/Ctrl+Enter
      if (mod && e.key === "Enter") {
        e.preventDefault();
        if (isRunning && onStop) {
          void onStop();
        } else if (!runDisabled) {
          void onRun();
        }
        return;
      }
      // Save: Cmd/Ctrl+S
      if (mod && (e.key.toLowerCase() === "s")) {
        e.preventDefault();
        if (!saveDisabled) void onSave();
        return;
      }
      // Format: Shift+Alt+F
      if (e.shiftKey && e.altKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        if (!formatDisabled) void onFormat();
      }
    };
    window.addEventListener("keydown", handler, { capture: true });
    return () => window.removeEventListener("keydown", handler, { capture: true } as any);
  }, [hotkeys, isRunning, runDisabled, saveDisabled, formatDisabled, onRun, onStop, onSave, onFormat]);

  return (
    <div
      className={cx(
        "flex items-center gap-2 p-2",
        "border-b border-[color:var(--divider,#e5e7eb)] bg-[var(--toolbar-bg,#f9fafb)]",
        "text-sm",
        className
      )}
      role="toolbar"
      aria-label="Editor actions"
    >
      {/* Run / Stop */}
      <button
        type="button"
        data-testid="editor-run"
        className={cx(
          "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium",
          "focus:outline-none focus:ring-2 focus:ring-offset-1",
          isRunning
            ? "bg-[var(--danger-bg,#fee2e2)] text-[var(--danger-fg,#991b1b)] hover:bg-[color:var(--danger-bg-h,#fecaca)]"
            : "bg-[var(--accent-bg,#e0f2fe)] text-[var(--accent-fg,#075985)] hover:bg-[color:var(--accent-bg-h,#bae6fd)]",
          runDisabled && "opacity-60 pointer-events-none"
        )}
        onClick={() => (isRunning && onStop ? onStop() : onRun())}
        aria-disabled={runDisabled}
        title={isRunning ? "Stop (Esc/⌘⏎)" : "Run (⌘/Ctrl+Enter)"}
      >
        {isRunning && onStop ? (
          <>
            <IconStop width="16" height="16" />
            Stop
          </>
        ) : (
          <>
            <IconPlay width="16" height="16" />
            Run
          </>
        )}
      </button>

      {/* Save */}
      <button
        type="button"
        data-testid="editor-save"
        className={cx(
          "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium",
          "focus:outline-none focus:ring-2 focus:ring-offset-1",
          "bg-[var(--btn-bg,#111827)] text-white hover:bg-[color:var(--btn-bg-h,#0b1220)]",
          saveDisabled && "opacity-60 pointer-events-none"
        )}
        onClick={() => onSave()}
        aria-disabled={saveDisabled}
        title="Save (⌘/Ctrl+S)"
      >
        <IconSave width="16" height="16" />
        Save
        {isDirty && (
          <span
            className={cx(
              "ml-1 inline-flex items-center rounded px-1 py-0.5 text-xs",
              "bg-[var(--warn-bg,#fef3c7)] text-[var(--warn-fg,#92400e)]"
            )}
            aria-label="Unsaved changes"
            title="Unsaved changes"
          >
            •
          </span>
        )}
      </button>

      {/* Format */}
      <button
        type="button"
        data-testid="editor-format"
        className={cx(
          "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium",
          "focus:outline-none focus:ring-2 focus:ring-offset-1",
          "bg-[var(--muted-bg,#f3f4f6)] text-[var(--fg,#111827)] hover:bg-[color:var(--hover-bg,#e5e7eb)]",
          formatDisabled && "opacity-60 pointer-events-none"
        )}
        onClick={() => onFormat()}
        aria-disabled={formatDisabled}
        title="Format (Shift+Alt+F)"
      >
        <IconFormat width="16" height="16" />
        Format
      </button>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Right slot (e.g., method picker, gas, network) */}
      {rightSlot && <div className="flex items-center gap-2">{rightSlot}</div>}
    </div>
  );
}

export default Toolbar;
