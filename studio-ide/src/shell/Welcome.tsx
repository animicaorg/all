import { useState } from "react";
import { useAuthStore } from "@/state/auth";

export function Welcome() {
  const { me, startAnon } = useAuthStore();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const anonOk = me?.methods?.anon ?? true;

  async function go() {
    setBusy(true);
    setErr(null);
    try {
      await startAnon();
    } catch (e: any) {
      setErr(e?.message || "Could not start a session.");
      setBusy(false);
    }
  }

  return (
    <div className="safe-t safe-b mx-auto grid h-full max-w-md place-items-center px-6">
      <div className="w-full">
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-accent/15 text-accent">
            <Logo />
          </div>
          <div>
            <h1 className="text-lg font-semibold leading-tight">Animica Studio</h1>
            <p className="text-sm text-muted">Code on GitHub with the ENA agent — from any device.</p>
          </div>
        </div>

        <div className="card p-5">
          <ul className="mb-5 space-y-2.5 text-sm text-muted">
            <Feat>Connect a GitHub repo and edit it from your phone</Feat>
            <Feat>Ask ENA to write code — review diffs, then commit &amp; push</Feat>
            <Feat>Live preview and a full terminal in your sandbox</Feat>
          </ul>

          {anonOk ? (
            <button className="btn-primary w-full" onClick={go} disabled={busy}>
              {busy ? "Starting…" : "Start coding"}
            </button>
          ) : (
            <a className="btn-primary w-full" href="/login">
              Sign in to continue
            </a>
          )}
          {anonOk && (
            <a className="mt-2 block text-center text-xs text-muted hover:text-fg" href="/login">
              or sign in with email
            </a>
          )}
          {err && <p className="mt-3 text-sm text-danger">{err}</p>}

          <p className="mt-4 text-center text-xs text-muted">
            Prefer the full desktop?{" "}
            <a className="text-accent underline-offset-2 hover:underline" href="/desktop">
              Open desktop Studio
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}

function Feat({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2">
      <svg className="mt-0.5 h-4 w-4 flex-none text-accent" viewBox="0 0 20 20" fill="currentColor">
        <path
          fillRule="evenodd"
          d="M16.7 5.3a1 1 0 0 1 0 1.4l-7.5 7.5a1 1 0 0 1-1.4 0L3.3 9.7a1 1 0 1 1 1.4-1.4l3.3 3.3 6.8-6.8a1 1 0 0 1 1.4 0z"
        />
      </svg>
      <span>{children}</span>
    </li>
  );
}

function Logo() {
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="6.5" />
      <circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none" />
    </svg>
  );
}
