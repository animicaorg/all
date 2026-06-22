import { useCallback, useEffect, useRef, useState } from "react";
import { useAuthStore } from "@/state/auth";
import { previewApi, PREVIEW_APP_URL, type PreviewStatus } from "@/services/previewApi";
import { ApiError } from "@/services/api";

export function PreviewPanel() {
  const me = useAuthStore((s) => s.me);
  const [status, setStatus] = useState<PreviewStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumping the key forces the iframe to reload (Refresh button).
  const [frameKey, setFrameKey] = useState(0);
  const pollRef = useRef<number | null>(null);

  const anon = me?.tier === "anon";

  const refreshStatus = useCallback(async () => {
    try {
      const s = await previewApi.status();
      setStatus(s);
      return s;
    } catch {
      // The container may not be up yet (no session) — treat as not running.
      setStatus(null);
      return null;
    }
  }, []);

  useEffect(() => {
    if (anon) return;
    void refreshStatus();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [anon, refreshStatus]);

  // While running, poll status so the log tail / listening flag stay fresh.
  useEffect(() => {
    if (!status?.running) {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (pollRef.current) return;
    pollRef.current = window.setInterval(() => void refreshStatus(), 3000);
    return () => {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [status?.running, refreshStatus]);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      await previewApi.start();
      const s = await refreshStatus();
      if (s?.running) setFrameKey((k) => k + 1);
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 404
          ? "No run command detected in this repo (looked for package.json dev/start or index.html)."
          : (e as Error)?.message || "Failed to start the dev server.";
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    setError(null);
    try {
      await previewApi.stop();
      await refreshStatus();
    } catch (e) {
      setError((e as Error)?.message || "Failed to stop the dev server.");
    } finally {
      setBusy(false);
    }
  };

  if (anon) {
    return (
      <div className="grid h-full place-items-center px-6">
        <div className="max-w-sm text-center">
          <PreviewIcon />
          <h2 className="text-base font-semibold">Sign in to run your project</h2>
          <p className="mt-1 text-sm text-muted">
            Run a dev server in your private sandbox and preview it live.
          </p>
        </div>
      </div>
    );
  }

  const running = !!status?.running;

  return (
    <div className="flex h-full flex-col bg-bg">
      <div className="flex flex-none flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-lg bg-accent/15 text-accent">
            <PreviewIcon small />
          </span>
          <span className="text-sm font-semibold">Preview</span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {running ? (
            <button className="btn-ghost btn-sm" disabled={busy} onClick={() => void stop()}>
              {busy ? "Stopping…" : "Stop"}
            </button>
          ) : (
            <button className="btn-primary btn-sm" disabled={busy} onClick={() => void run()}>
              {busy ? "Starting…" : "Run"}
            </button>
          )}
          {running && (
            <>
              <button
                className="btn-ghost btn-sm"
                onClick={() => setFrameKey((k) => k + 1)}
                aria-label="Refresh preview"
                title="Refresh"
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" />
                </svg>
              </button>
              <a
                className="btn-ghost btn-sm"
                href={PREVIEW_APP_URL}
                target="_blank"
                rel="noreferrer"
                aria-label="Open preview in a new tab"
                title="Open in new tab"
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
                </svg>
              </a>
            </>
          )}
        </div>
      </div>

      {/* Status / log line */}
      <div className="flex flex-none items-center gap-2 border-b border-border px-3 py-1.5 text-xs text-muted">
        <StatusDot running={running} listening={status?.listening} />
        {running ? (
          <span className="truncate font-mono">{status?.cmd || "dev server"}</span>
        ) : (
          <span>Not running</span>
        )}
        {error && <span className="ml-auto truncate text-danger">{error}</span>}
      </div>

      <div className="relative min-h-0 flex-1">
        {running ? (
          <iframe
            key={frameKey}
            src={PREVIEW_APP_URL}
            title="Preview"
            className="h-full w-full border-0 bg-white"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
          />
        ) : (
          <div className="grid h-full place-items-center px-6 text-center">
            <div className="max-w-xs">
              <h2 className="text-base font-semibold">Run your project</h2>
              <p className="mt-1 text-sm text-muted">
                Starts your dev server (npm dev/start or a static server) in your sandbox and shows it
                here.
              </p>
              <button className="btn-primary btn-sm mt-4" disabled={busy} onClick={() => void run()}>
                {busy ? "Starting…" : "Run"}
              </button>
            </div>
          </div>
        )}
      </div>

      {running && status?.logTail && (
        <pre className="safe-b max-h-24 flex-none overflow-y-auto border-t border-border bg-surface px-3 py-2 font-mono text-[11px] leading-snug text-muted">
          {status.logTail}
        </pre>
      )}
    </div>
  );
}

function StatusDot({ running, listening }: { running: boolean; listening?: boolean }) {
  const cls = !running ? "bg-muted" : listening ? "bg-ok" : "bg-warn";
  return <span className={`h-2 w-2 flex-none rounded-full ${cls}`} />;
}

function PreviewIcon({ small }: { small?: boolean }) {
  const cls = small ? "h-4 w-4" : "mx-auto mb-4 h-12 w-12 rounded-2xl bg-accent/15 p-3 text-accent";
  return (
    <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="4" width="18" height="14" rx="2" />
      <path d="M3 8h18M8 21h8" />
    </svg>
  );
}
