import { useEffect, useMemo, useState } from "react";
import { useGithubStore } from "@/state/github";
import type { Repo } from "@/services/githubApi";

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const s = Math.max(1, Math.round((Date.now() - then) / 1000));
  const units: [number, string][] = [
    [60, "s"],
    [60, "m"],
    [24, "h"],
    [30, "d"],
    [12, "mo"],
    [Number.POSITIVE_INFINITY, "y"],
  ];
  let v = s;
  let label = "s";
  for (const [div, l] of units) {
    if (v < div) {
      label = l;
      break;
    }
    v = Math.floor(v / div);
    label = l;
  }
  return `${v}${label} ago`;
}

export function RepoPicker() {
  const { repos, reposLoading, listRepos, openRepo, opening, error, login, disconnect } =
    useGithubStore();
  const [q, setQ] = useState("");
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    if (repos.length === 0) void listRepos();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return repos;
    return repos.filter((r) => r.full_name.toLowerCase().includes(needle));
  }, [repos, q]);

  const open = async (r: Repo) => {
    setPending(r.full_name);
    try {
      await openRepo(r.full_name);
    } catch {
      /* error in store */
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="mx-auto flex h-full w-full max-w-md flex-col px-4 pt-4">
      <div className="flex flex-none items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Open a repository</h1>
          {login && <p className="text-xs text-muted">@{login}</p>}
        </div>
        <button className="btn-ghost btn-sm" onClick={() => void disconnect()}>
          Disconnect
        </button>
      </div>

      <input
        className="input mt-3 flex-none"
        placeholder="Search repositories…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
      />

      {error && <p className="mt-3 flex-none text-sm text-danger">{error}</p>}

      <div className="mt-3 min-h-0 flex-1 overflow-y-auto pb-6">
        {reposLoading && repos.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted">Loading repositories…</div>
        ) : filtered.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted">No repositories match.</div>
        ) : (
          <ul className="space-y-2">
            {filtered.map((r) => {
              const isOpening = opening && pending === r.full_name;
              return (
                <li key={r.full_name}>
                  <button
                    className="card flex w-full items-center gap-3 p-3 text-left transition-colors hover:border-accent/50 disabled:opacity-60"
                    onClick={() => void open(r)}
                    disabled={opening}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{r.name}</span>
                        {r.private && <span className="chip">private</span>}
                      </div>
                      <div className="truncate text-xs text-muted">
                        {r.full_name} · {relativeTime(r.updated_at)}
                      </div>
                    </div>
                    <span className="flex-none text-xs text-accent">
                      {isOpening ? "Opening…" : "Open"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
