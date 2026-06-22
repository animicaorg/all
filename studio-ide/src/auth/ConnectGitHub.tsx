import { useState } from "react";
import { useGithubStore } from "@/state/github";
import { useAuthStore } from "@/state/auth";

const TOKEN_URL =
  "https://github.com/settings/tokens/new?scopes=repo&description=Animica%20Studio";

export function ConnectGitHub() {
  const { connect, loading, error, clearError } = useGithubStore();
  const isAnon = useAuthStore((s) => s.me?.tier === "anon");
  const [token, setToken] = useState("");

  if (isAnon) return <AnonNotice />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token.trim()) return;
    try {
      await connect(token);
      setToken("");
    } catch {
      /* error surfaced via store */
    }
  };

  return (
    <div className="mx-auto flex h-full w-full max-w-md flex-col justify-center px-5 py-8">
      <div className="mb-5 flex items-center gap-3">
        <span className="grid h-11 w-11 place-items-center rounded-2xl bg-accent/15 text-accent">
          <svg viewBox="0 0 24 24" className="h-6 w-6" fill="currentColor">
            <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.1-1.47-1.1-1.47-.9-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.52 2.34 1.08 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02a9.5 9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2z" />
          </svg>
        </span>
        <div>
          <h1 className="text-lg font-semibold">Connect GitHub</h1>
          <p className="text-sm text-muted">Bring your repositories into Studio.</p>
        </div>
      </div>

      <form onSubmit={submit} className="card p-4">
        <label className="mb-1.5 block text-sm font-medium">Personal access token</label>
        <input
          className="input font-mono"
          type="password"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="ghp_…"
          value={token}
          onChange={(e) => {
            setToken(e.target.value);
            if (error) clearError();
          }}
        />
        <p className="mt-2 text-xs text-muted">
          Create a token with the <span className="chip">repo</span> scope.{" "}
          <a className="text-accent underline" href={TOKEN_URL} target="_blank" rel="noreferrer">
            Generate one on GitHub →
          </a>
        </p>

        {error && <p className="mt-3 text-sm text-danger">{error}</p>}

        <button className="btn-primary mt-4 w-full" type="submit" disabled={loading || !token.trim()}>
          {loading ? "Connecting…" : "Connect"}
        </button>
      </form>

      {/* Reserved slot — OAuth lands in a later increment. */}
      <button
        className="btn-ghost mt-3 w-full cursor-not-allowed opacity-50"
        disabled
        title="Coming soon"
      >
        Connect with GitHub (OAuth) — coming soon
      </button>

      <p className="mt-4 px-1 text-center text-xs text-muted">
        Your token is encrypted on the server and never stored in your repository.
      </p>
    </div>
  );
}

function AnonNotice() {
  return (
    <div className="grid h-full place-items-center px-6 text-center">
      <div className="max-w-sm">
        <h1 className="text-lg font-semibold">Sign in to connect GitHub</h1>
        <p className="mt-2 text-sm text-muted">
          Connecting a GitHub account isn't available for guest sessions. Create an account to link
          your repositories and edit private code.
        </p>
      </div>
    </div>
  );
}
