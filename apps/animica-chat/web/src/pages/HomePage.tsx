import { Link } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { ECOSYSTEM_LINKS, SiteHeader } from '../components/SiteHeader';

export function HomePage() {
  const me = useAuth((s) => s.me);
  return (
    <div className="min-h-full bg-ink-950 text-ink-50">
      <SiteHeader />
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6 sm:py-24">
        <section className="text-center">
          <span className="inline-flex items-center gap-2 rounded-full border border-white/8 bg-white/[0.03] px-3 py-1 text-xs tracking-wide text-ink-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Now in early access
          </span>
          <h1 className="mt-6 text-balance text-4xl font-semibold tracking-tight sm:text-6xl">
            AI tools for builders, creators,
            <br className="hidden sm:inline" /> and the Animica ecosystem.
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-balance text-lg text-ink-300">
            Animica Chat is a $10/month AI workspace. Powerful chat, coding assistant with
            <strong> GitHub + GitLab </strong> tools, content & creator workflows, and an Animica
            ecosystem helper. <span className="text-ink-100">No ANM required.</span>
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link
              to={me ? '/chat' : '/login'}
              className="rounded-full bg-accent-600 px-6 py-3 text-sm font-medium text-white shadow-glow transition hover:bg-accent-500"
            >
              {me ? 'Open chat' : 'Start chatting'}
            </Link>
            <Link
              to="/pricing"
              className="rounded-full border border-white/10 bg-white/5 px-6 py-3 text-sm font-medium text-ink-100 hover:bg-white/10"
            >
              See pricing
            </Link>
          </div>
          <p className="mt-3 text-xs text-ink-500">Pay with PayPal · Cancel anytime · No crypto required</p>

          {/* Quick-access pair: Explorer + Mine. Sits above the fold so
              returning visitors who came to inspect the chain or join
              the pool find them immediately, without having to scroll
              past the chat-focused hero. */}
          <div className="mx-auto mt-10 grid max-w-2xl gap-3 sm:grid-cols-2">
            <a
              href="https://explorer.animica.org"
              target="_blank"
              rel="noreferrer"
              className="group flex items-center gap-3 rounded-2xl border border-white/8 bg-white/[0.02] p-4 text-left transition-colors hover:border-accent-500/40 hover:bg-white/[0.04]"
            >
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-accent-500/30 to-accent-700/30 text-accent-400">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-5 w-5">
                  <circle cx="11" cy="11" r="7" />
                  <path d="m20 20-3.5-3.5" strokeLinecap="round" />
                </svg>
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-sm font-semibold text-ink-50 group-hover:text-accent-400">
                  Explorer
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5 opacity-60">
                    <path d="M7 17 17 7M9 7h8v8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <div className="text-xs text-ink-400">Blocks, txs, addresses · live mainnet</div>
              </div>
            </a>
            <a
              href="https://pool.animica.org"
              target="_blank"
              rel="noreferrer"
              className="group flex items-center gap-3 rounded-2xl border border-white/8 bg-white/[0.02] p-4 text-left transition-colors hover:border-accent-500/40 hover:bg-white/[0.04]"
            >
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-emerald-500/25 to-emerald-700/25 text-emerald-300">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-5 w-5">
                  <path d="m4 7 8-4 8 4-8 4z" strokeLinejoin="round" />
                  <path d="m4 12 8 4 8-4" strokeLinejoin="round" />
                  <path d="m4 17 8 4 8-4" strokeLinejoin="round" />
                </svg>
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-sm font-semibold text-ink-50 group-hover:text-accent-400">
                  Mining pool
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5 opacity-60">
                    <path d="M7 17 17 7M9 7h8v8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <div className="text-xs text-ink-400">Useful-work pool · stratum on pool.animica.org:3333</div>
              </div>
            </a>
          </div>
        </section>

        <section className="mx-auto mt-20 grid max-w-5xl gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {features.map((f) => (
            <div key={f.title} className="rounded-xl border border-white/8 bg-white/[0.02] p-5">
              <div className="mb-2 text-sm font-semibold text-ink-50">{f.title}</div>
              <p className="text-sm text-ink-300">{f.body}</p>
            </div>
          ))}
        </section>

        <section className="mx-auto mt-20 max-w-5xl">
          <div className="flex items-end justify-between gap-3 px-1">
            <h2 className="text-lg font-semibold tracking-tight sm:text-xl">Explore Animica</h2>
            <a
              href="https://animica.org"
              className="text-xs text-ink-400 hover:text-ink-200"
            >
              animica.org →
            </a>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {ECOSYSTEM_LINKS.map((it) => (
              <a
                key={it.href}
                href={it.href}
                className="group rounded-xl border border-white/8 bg-white/[0.02] p-4 transition-colors hover:border-accent-500/40 hover:bg-white/[0.04]"
              >
                <div className="text-sm font-semibold text-ink-50 group-hover:text-accent-400">
                  {it.label}
                </div>
                {it.description && (
                  <div className="mt-1 text-xs text-ink-400 group-hover:text-ink-300">
                    {it.description}
                  </div>
                )}
              </a>
            ))}
          </div>
        </section>

        <section className="mx-auto mt-20 max-w-3xl rounded-2xl border border-white/8 bg-white/[0.02] p-6 text-center sm:p-8">
          <h2 className="text-2xl font-semibold">Animica Chat Pro</h2>
          <p className="mt-2 text-ink-300">$10/month · 1,000 messages every week · All assistant modes · Agent tools</p>
          <div className="mt-6">
            <Link
              to={me ? '/billing' : '/login'}
              className="inline-block rounded-full bg-accent-600 px-6 py-3 text-sm font-medium text-white shadow-glow hover:bg-accent-500"
            >
              {me ? 'Manage subscription' : 'Subscribe with PayPal'}
            </Link>
          </div>
        </section>
      </main>
      <footer className="border-t border-white/5 py-8 text-center text-xs text-ink-500">
        © Animica · <a href="https://animica.org" className="hover:text-ink-300">animica.org</a> ·
        <Link to="/pricing" className="ml-2 hover:text-ink-300">Pricing</Link>
      </footer>
    </div>
  );
}

const features = [
  {
    title: 'Chat that ships',
    body: 'Smooth streaming, markdown + syntax-highlighted code, keyboard shortcuts, regenerate / edit / copy.',
  },
  {
    title: 'Coding agent',
    body: 'Link GitHub or GitLab and the coding-mode assistant reads files and opens draft PRs / MRs on your behalf.',
  },
  {
    title: 'Animica-aware',
    body: 'Built-in helper for the Animica L1: wallet, explorer, node, mining, ecosystem apps.',
  },
  {
    title: 'Creator workflows',
    body: 'Marketing copy, business plans, art prompts — switch assistant mode per conversation.',
  },
  {
    title: 'Pay with PayPal',
    body: 'Standard USD subscription. No tokens, no wallets required to use the product.',
  },
  {
    title: 'Private by default',
    body: 'Conversations are encrypted at rest and never shared with third-party model providers beyond inference.',
  },
];
