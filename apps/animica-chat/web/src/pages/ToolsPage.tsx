import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { SiteHeader } from '../components/SiteHeader';

interface Status {
  github: { login: string; scopes: string[]; selectedRepo: string | null } | null;
  gitlab: { username: string; host: string; scopes: string[] } | null;
}

interface Repo {
  fullName: string;
  private: boolean;
  defaultBranch: string;
  description: string | null;
  url: string;
  pushedAt: string | null;
}

export function ToolsPage() {
  const [status, setStatus] = useState<Status | null>(null);

  async function load() {
    const r = await api.get<Status>('/api/integrations/status');
    setStatus(r);
  }
  useEffect(() => { load(); }, []);

  async function disconnect(provider: 'github' | 'gitlab') {
    await api.post(`/api/integrations/${provider}/disconnect`);
    load();
  }

  return (
    <div className="min-h-full bg-ink-950 text-ink-50">
      <SiteHeader />
      <main className="mx-auto mt-12 max-w-3xl px-4">
        <h1 className="text-2xl font-semibold">Coding agent tools</h1>
        <p className="mt-1 text-ink-400">Link GitHub or GitLab so the coding-mode assistant can read repos and open draft PRs/MRs on your behalf.</p>
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <ProviderCard
            name="GitHub"
            linked={!!status?.github}
            detail={status?.github ? `@${status.github.login} · ${status.github.scopes.join(', ')}` : 'Not linked'}
            linkHref="/api/integrations/github/start"
            onDisconnect={() => disconnect('github')}
          />
          <ProviderCard
            name="GitLab"
            linked={!!status?.gitlab}
            detail={status?.gitlab ? `@${status.gitlab.username} · ${status.gitlab.host}` : 'Not linked'}
            linkHref="/api/integrations/gitlab/start"
            onDisconnect={() => disconnect('gitlab')}
          />
        </div>

        {status?.github && (
          <GitHubRepoPicker
            selected={status.github.selectedRepo}
            onChange={(repo) => {
              setStatus((s) =>
                s?.github ? { ...s, github: { ...s.github, selectedRepo: repo } } : s,
              );
            }}
          />
        )}

        <div className="mt-8 rounded-xl border border-white/8 bg-white/[0.02] p-4 text-sm text-ink-300">
          <strong className="text-ink-100">How it works.</strong> Read-only actions (list repos, read a file) run automatically. Write actions (open a draft PR/MR) surface a confirm card in the chat so you approve the diff before anything ships. Tokens are encrypted at rest.
        </div>
      </main>
    </div>
  );
}

function GitHubRepoPicker({
  selected,
  onChange,
}: {
  selected: string | null;
  onChange: (repo: string | null) => void;
}) {
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ repos: Repo[] }>('/api/integrations/github/repos?perPage=100')
      .then((r) => {
        if (!cancelled) setRepos(r.repos);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function save(fullName: string | null) {
    setBusy(true);
    try {
      const r = await api.post<{ selected: string | null }>('/api/integrations/github/repo', {
        fullName,
      });
      onChange(r.selected);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const filtered = (repos ?? []).filter((r) =>
    filter ? r.fullName.toLowerCase().includes(filter.toLowerCase()) : true,
  );

  return (
    <div className="mt-6 rounded-xl border border-white/8 bg-white/[0.02] p-5">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold">Default repo</h2>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-300">
              Read
            </span>
            <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-300">
              Write (draft PRs)
            </span>
            <span className="text-xs text-ink-400">scope: <code className="text-ink-300">repo</code></span>
          </div>
          <p className="mt-2 text-xs text-ink-400">
            The coding-mode agent reads files automatically; write actions (commits + draft PRs) surface a confirm card in chat before anything ships.
          </p>
        </div>
        {selected && (
          <button
            type="button"
            onClick={() => save(null)}
            disabled={busy}
            className="shrink-0 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-xs text-ink-300 hover:bg-white/10 disabled:opacity-50"
          >
            Clear
          </button>
        )}
      </div>

      {selected && (
        <div className="mt-3 inline-flex items-center gap-2 rounded-full border border-accent-500/30 bg-accent-500/10 px-3 py-1 text-xs text-accent-200">
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor"><path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56v-2.02c-3.2.7-3.88-1.36-3.88-1.36-.52-1.34-1.28-1.69-1.28-1.69-1.05-.72.08-.71.08-.71 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.29 1.18-3.1-.12-.29-.51-1.46.11-3.04 0 0 .97-.31 3.17 1.18.92-.26 1.91-.39 2.89-.39.98 0 1.97.13 2.89.39 2.2-1.49 3.17-1.18 3.17-1.18.62 1.58.23 2.75.11 3.04.74.81 1.18 1.84 1.18 3.1 0 4.43-2.7 5.41-5.27 5.69.41.36.78 1.06.78 2.14v3.18c0 .31.21.68.8.56C20.21 21.38 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" /></svg>
          {selected}
        </div>
      )}

      <input
        type="text"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter repos…"
        className="mt-4 w-full rounded-md border border-white/8 bg-ink-900/40 px-3 py-2 text-sm text-ink-100 placeholder:text-ink-500 focus:border-accent-500 focus:outline-none"
      />

      <div className="mt-3 max-h-80 overflow-y-auto rounded-md border border-white/5">
        {error && (
          <p className="px-3 py-2 text-xs text-rose-300">Failed to load repos: {error}</p>
        )}
        {repos === null && !error && (
          <p className="px-3 py-2 text-xs text-ink-500">Loading your repos…</p>
        )}
        {repos !== null && filtered.length === 0 && (
          <p className="px-3 py-2 text-xs text-ink-500">No matching repos.</p>
        )}
        <ul className="divide-y divide-white/5">
          {filtered.map((r) => (
            <li key={r.fullName} className="flex items-center gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm text-ink-100">{r.fullName}</span>
                  {r.private && (
                    <span className="rounded-full bg-white/10 px-1.5 py-0.5 text-[10px] text-ink-300">private</span>
                  )}
                </div>
                {r.description && (
                  <p className="mt-0.5 truncate text-xs text-ink-500">{r.description}</p>
                )}
              </div>
              <button
                type="button"
                onClick={() => save(r.fullName)}
                disabled={busy || selected === r.fullName}
                className={
                  selected === r.fullName
                    ? 'shrink-0 rounded-full border border-accent-500/40 bg-accent-500/15 px-2.5 py-1 text-[11px] text-accent-200'
                    : 'shrink-0 rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-ink-200 hover:bg-white/10 disabled:opacity-50'
                }
              >
                {selected === r.fullName ? 'Selected' : 'Use this'}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ProviderCard({ name, linked, detail, linkHref, onDisconnect }: {
  name: string; linked: boolean; detail: string; linkHref: string; onDisconnect: () => void;
}) {
  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.02] p-5">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">{name}</h2>
        {linked ? (
          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">Linked</span>
        ) : (
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs text-ink-300">Not linked</span>
        )}
      </div>
      <p className="mt-2 text-xs text-ink-400">{detail}</p>
      {linked ? (
        <button type="button" onClick={onDisconnect} className="mt-3 rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-xs hover:bg-white/10">
          Disconnect
        </button>
      ) : (
        <a href={linkHref} className="mt-3 inline-block rounded-md bg-accent-600 px-3 py-1.5 text-xs text-white hover:bg-accent-500">
          Link {name}
        </a>
      )}
    </div>
  );
}
