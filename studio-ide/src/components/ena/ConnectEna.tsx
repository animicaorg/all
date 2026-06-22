import { useState } from "react";
import { useEnaStore } from "@/state/ena";

export function ConnectEna() {
  const { connect, busy, error } = useEnaStore();
  const [key, setKey] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!key.trim()) return;
    await connect(key);
  }

  return (
    <div className="mx-auto flex h-full max-w-md flex-col justify-center px-5">
      <div className="mb-4 flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-2xl bg-accent/15 text-accent">
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </span>
        <div>
          <h2 className="text-base font-semibold leading-tight">Connect ENA</h2>
          <p className="text-sm text-muted">Use your own Animica pool key for AI coding.</p>
        </div>
      </div>

      <form onSubmit={submit} className="card p-4">
        <label className="mb-1 block text-xs font-medium text-muted">Pool API key</label>
        <input
          className="input font-mono"
          type="password"
          inputMode="text"
          autoComplete="off"
          placeholder="anm_…"
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button className="btn-primary mt-3 w-full" disabled={busy || !key.trim()}>
          {busy ? "Connecting…" : "Connect ENA"}
        </button>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        <p className="mt-3 text-center text-xs text-muted">
          Get a key at{" "}
          <a className="text-accent hover:underline" href="https://pool.animica.org/keys" target="_blank" rel="noreferrer">
            pool.animica.org/keys
          </a>
          . Usage is billed to your pool account.
        </p>
      </form>
    </div>
  );
}
