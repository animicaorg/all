import { useEffect, useState } from "react";
import { useScmStore } from "@/state/scm";
import { useGithubStore } from "@/state/github";
import { useAuthStore } from "@/state/auth";
import { useUiStore } from "@/state/ui";
import { useFilesStore } from "@/state/files";
import { DiffView } from "@/components/ena/DiffView";

// Source Control — rendered as a mobile-first bottom sheet / side panel overlay.
export function ScmPanel() {
  const { closeScm } = useUiStore();
  const {
    changedFiles,
    loading,
    committing,
    pushing,
    error,
    notice,
    ahead,
    branch,
    refresh,
    commit,
    push,
    loadDiff,
    diffByPath,
    clearNotice,
  } = useScmStore();
  const { connected } = useGithubStore();
  const { openFile } = useFilesStore();
  const setPanel = useUiStore((s) => s.setPanel);
  const me = useAuthStore((s) => s.me);

  const [message, setMessage] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const isAnon = me?.tier === "anon";
  const pushDisabled = isAnon || !connected || pushing;
  const pushReason = isAnon
    ? "Sign in to push"
    : !connected
      ? "Connect GitHub to push"
      : undefined;

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onCommit = async () => {
    const ok = await commit(message);
    if (ok) setMessage("");
  };

  const toggle = (path: string) => {
    setExpanded((cur) => {
      const next = cur === path ? null : path;
      if (next) void loadDiff(path);
      return next;
    });
  };

  const open = (path: string) => {
    void openFile(path);
    setPanel("editor");
    closeScm();
  };

  return (
    <div className="fixed inset-0 z-30 flex flex-col justify-end sm:items-end sm:justify-stretch">
      <div className="absolute inset-0 bg-black/50" onClick={closeScm} />
      <div className="relative flex max-h-[85vh] w-full flex-col rounded-t-2xl border border-border bg-surface shadow-2xl sm:h-full sm:max-h-full sm:w-[420px] sm:rounded-none sm:border-y-0 sm:border-r-0">
        {/* header */}
        <div className="safe-t flex flex-none items-center gap-2 border-b border-border px-3 py-3">
          <svg viewBox="0 0 24 24" className="h-5 w-5 text-accent" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="6" cy="6" r="2.5" />
            <circle cx="6" cy="18" r="2.5" />
            <circle cx="18" cy="8" r="2.5" />
            <path d="M6 8.5v7M18 10.5c0 3-3 3.5-6 3.5" />
          </svg>
          <div className="min-w-0">
            <div className="text-sm font-semibold">Source Control</div>
            {branch && (
              <div className="truncate text-xs text-muted">
                {branch}
                {ahead > 0 && <span className="text-warn"> · {ahead} to push</span>}
              </div>
            )}
          </div>
          <button className="btn-ghost btn-sm ml-auto" onClick={() => void refresh()} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
          <button className="btn-ghost btn-sm" onClick={closeScm} aria-label="Close">
            ✕
          </button>
        </div>

        {/* changed files */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading && changedFiles.length === 0 ? (
            <div className="p-4 text-sm text-muted">Loading changes…</div>
          ) : changedFiles.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted">
              No changes. Your working tree is clean.
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {changedFiles.map((f) => (
                <li key={f.path}>
                  <div className="flex items-center gap-2 px-3 py-2">
                    <StatusBadge status={f.status} />
                    <button
                      className="min-w-0 flex-1 truncate text-left font-mono text-xs hover:text-accent"
                      onClick={() => open(f.path)}
                      title={f.path}
                    >
                      {f.path}
                    </button>
                    <button
                      className="btn-ghost btn-sm flex-none"
                      onClick={() => toggle(f.path)}
                      aria-label="Toggle diff"
                    >
                      {expanded === f.path ? "Hide" : "Diff"}
                    </button>
                  </div>
                  {expanded === f.path && (
                    <div className="border-t border-border bg-bg">
                      {diffByPath[f.path] === undefined ? (
                        <div className="px-3 py-2 text-xs text-muted">Loading diff…</div>
                      ) : diffByPath[f.path].trim() === "" ? (
                        <div className="px-3 py-2 text-xs text-muted">No textual diff (binary or new file).</div>
                      ) : (
                        <DiffFromPatch patch={diffByPath[f.path]} path={f.path} />
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* notices */}
        {(error || notice) && (
          <div
            className={`flex-none px-3 py-2 text-xs ${error ? "text-danger" : "text-ok"}`}
            onClick={clearNotice}
          >
            {error || notice}
          </div>
        )}

        {/* commit + push */}
        <div className="safe-b flex-none border-t border-border p-3">
          <textarea
            className="input mb-2 resize-none"
            rows={2}
            placeholder="Commit message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <div className="flex items-center gap-2">
            <button
              className="btn-ghost btn-sm flex-1"
              onClick={onCommit}
              disabled={committing || !message.trim() || changedFiles.length === 0}
            >
              {committing ? "Committing…" : "Commit"}
            </button>
            <button
              className="btn-primary btn-sm flex-1"
              onClick={() => void push()}
              disabled={pushDisabled}
              title={pushReason}
            >
              {pushing ? "Pushing…" : "Push"}
            </button>
          </div>
          {pushReason && <div className="mt-1.5 text-center text-[11px] text-muted">{pushReason}</div>}
        </div>
      </div>
    </div>
  );
}

// Render a git unified-patch string as a read-only diff. We reconstruct the
// old/new sides from the @@ hunks so DiffView can highlight them.
function DiffFromPatch({ patch, path }: { patch: string; path: string }) {
  const { oldText, newText } = parsePatch(patch);
  return <DiffView path={path} oldText={oldText} newText={newText} />;
}

function parsePatch(patch: string): { oldText: string; newText: string } {
  const oldLines: string[] = [];
  const newLines: string[] = [];
  let inHunk = false;
  for (const line of patch.split("\n")) {
    if (line.startsWith("@@")) {
      inHunk = true;
      continue;
    }
    if (!inHunk) continue;
    if (line.startsWith("\\")) continue; // "\ No newline at end of file"
    const tag = line[0];
    const text = line.slice(1);
    if (tag === "+") newLines.push(text);
    else if (tag === "-") oldLines.push(text);
    else {
      oldLines.push(text);
      newLines.push(text);
    }
  }
  return { oldText: oldLines.join("\n"), newText: newLines.join("\n") };
}

function StatusBadge({ status }: { status: string }) {
  const s = (status || "").trim();
  const map: Record<string, { label: string; cls: string }> = {
    M: { label: "M", cls: "text-warn" },
    A: { label: "A", cls: "text-ok" },
    D: { label: "D", cls: "text-danger" },
    R: { label: "R", cls: "text-accent" },
    "??": { label: "U", cls: "text-ok" },
  };
  const key = s.includes("?") ? "??" : s.replace(/[^MADR]/g, "").charAt(0) || "M";
  const m = map[key] ?? { label: key, cls: "text-muted" };
  return (
    <span className={`grid h-5 w-5 flex-none place-items-center rounded text-[11px] font-bold ${m.cls}`}>
      {m.label}
    </span>
  );
}
