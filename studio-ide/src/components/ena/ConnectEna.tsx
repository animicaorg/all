import { useState } from "react";
import { useEnaStore } from "@/state/ena";

export function ConnectEna({ onBack }: { onBack?: () => void }) {
  const { connect, busy, error, free, buyUrl } = useEnaStore();
  const [key, setKey] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!key.trim()) return;
    await connect(key);
  }

  const exhausted = free.enabled && free.remaining <= 0;

  return (
    <div className="mx-auto flex h-full max-w-md flex-col justify-center px-5">
      {onBack && (
        <button className="mb-3 self-start text-sm text-muted hover:text-fg" onClick={onBack}>
          ← Back to chat
        </button>
      )}

      <div className="mb-4 flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-2xl bg-accent/15 text-accent">
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </span>
        <div>
          <h2 className="text-base font-semibold leading-tight">
            {exhausted ? "Out of free messages" : "Connect ENA"}
          </h2>
          <p className="text-sm text-muted">
            {exhausted
              ? "Get your own key to keep coding with ENA."
              : "Use your own Animica pool key for AI coding."}
          </p>
        </div>
      </div>

      {/* Get-a-key CTA (buy) */}
      <a className="btn-primary w-full" href={buyUrl} target="_blank" rel="noreferrer">
        Get an API key
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M7 17 17 7M9 7h8v8" />
        </svg>
      </a>

      <div className="my-3 flex items-center gap-3 text-xs text-muted">
        <span className="h-px flex-1 bg-border" /> then paste it <span className="h-px flex-1 bg-border" />
      </div>

      <form onSubmit={submit} className="card p-4">
        <label className="mb-1 block text-xs font-medium text-muted">Pool API key</label>
        <input
          className="input font-mono"
          type="password"
          autoComplete="off"
          placeholder="anm_…"
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button className="btn-ghost mt-3 w-full" disabled={busy || !key.trim()}>
          {busy ? "Connecting…" : "Connect key"}
        </button>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        <p className="mt-3 text-center text-xs text-muted">
          {free.enabled && free.remaining > 0
            ? `You still have ${free.remaining} free message${free.remaining === 1 ? "" : "s"} today.`
            : "Usage is billed to your pool account."}
        </p>
      </form>
    </div>
  );
}
