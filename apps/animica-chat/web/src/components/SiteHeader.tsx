// Top nav with the Animica ecosystem links + Sign in / Open chat.
//
// Mobile behaviour: external links collapse behind a "More" dropdown so
// the nav stays one row on small screens. The chat / pricing / auth
// CTAs stay visible at all widths.

import { useEffect, useRef, useState } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { useAuth } from '../lib/auth';

export const ECOSYSTEM_LINKS: { label: string; href: string; description?: string }[] = [
  { label: 'Animica.org', href: 'https://animica.org', description: 'Project home + docs' },
  { label: 'Developers', href: 'https://animica.org/developers', description: 'SDKs, RPC, contracts' },
  { label: 'Wallet', href: 'https://animica.org/wallet', description: 'Desktop + mobile + browser' },
  { label: 'Buy ANM', href: 'https://buy.animica.org', description: 'Hosted OTC desk' },
  { label: 'Mine', href: 'https://pool.animica.org', description: 'Useful-work pool' },
  { label: 'Explorer', href: 'https://explorer.animica.org', description: 'Blocks, txs, addresses' },
  { label: 'AICF', href: 'https://aicf.animica.org', description: 'Compute marketplace' },
  { label: 'Docs', href: 'https://animica.org/docs', description: 'Operator + developer docs' },
  { label: 'Status', href: 'https://animica.org/status', description: 'Network health' },
];

function UserMenu() {
  const me = useAuth((s) => s.me);
  const logout = useAuth((s) => s.logout);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!me) return null;
  const initials = (me.user.name || me.user.email || '?')
    .trim()
    .split(/\s+|@/)[0]
    .slice(0, 2)
    .toUpperCase();
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="grid h-9 w-9 place-items-center rounded-full border border-white/8 bg-white/[0.05] text-[11px] font-semibold text-ink-100 hover:bg-white/[0.09]"
        title={me.user.email}
      >
        {initials}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full mt-2 w-56 rounded-xl border border-white/8 bg-ink-900/95 p-1 shadow-2xl backdrop-blur"
        >
          <div className="px-3 py-2 text-[11px] text-ink-500">
            Signed in as
            <div className="truncate text-ink-200">{me.user.email}</div>
          </div>
          <div className="my-1 border-t border-white/5" />
          <Link
            to="/account"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block rounded-lg px-3 py-2 text-sm text-ink-200 hover:bg-white/[0.05] hover:text-ink-50"
          >
            Account
          </Link>
          <Link
            to="/billing"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block rounded-lg px-3 py-2 text-sm text-ink-200 hover:bg-white/[0.05] hover:text-ink-50"
          >
            Billing
          </Link>
          <Link
            to="/tools"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block rounded-lg px-3 py-2 text-sm text-ink-200 hover:bg-white/[0.05] hover:text-ink-50"
          >
            Tools (GitHub / GitLab)
          </Link>
          <div className="my-1 border-t border-white/5" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              logout();
            }}
            className="block w-full rounded-lg px-3 py-2 text-left text-sm text-rose-300 hover:bg-rose-500/10 hover:text-rose-200"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function SiteHeader() {
  const me = useAuth((s) => s.me);
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  // Close the "More" menu on outside-click / Esc.
  useEffect(() => {
    if (!moreOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!moreRef.current?.contains(e.target as Node)) setMoreOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMoreOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [moreOpen]);

  return (
    <header className="sticky top-0 z-20 border-b border-white/5 bg-ink-950/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-2 px-3 py-2.5 sm:gap-4 sm:px-6 sm:py-3">
        <Link
          to="/"
          className="flex shrink-0 items-center gap-2 text-sm font-semibold tracking-tight"
        >
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-accent-500 to-accent-700 text-xs">
            A
          </span>
          <span className="hidden sm:inline">Animica Chat</span>
          <span className="sm:hidden">Animica</span>
        </Link>

        <nav className="flex flex-1 items-center justify-end gap-2 text-sm text-ink-300 sm:gap-3">
          {/* Inline ecosystem links — visible from md upward. */}
          <div className="hidden items-center gap-3 md:flex">
            {ECOSYSTEM_LINKS.slice(1, 5).map((it) => (
              <a key={it.href} href={it.href} className="hover:text-ink-50">
                {it.label}
              </a>
            ))}
          </div>

          {/* "More" dropdown — exposes the rest on any screen size, and
              acts as the full nav on mobile. */}
          <div ref={moreRef} className="relative">
            <button
              type="button"
              onClick={() => setMoreOpen((v) => !v)}
              aria-expanded={moreOpen}
              aria-haspopup="menu"
              className="grid h-9 w-9 place-items-center rounded-md border border-white/8 bg-white/[0.03] text-ink-200 hover:bg-white/[0.07] md:h-auto md:w-auto md:border-0 md:bg-transparent md:px-2 md:py-1 md:hover:text-ink-50"
            >
              <span className="hidden md:inline">More</span>
              <svg viewBox="0 0 24 24" className="h-4 w-4 md:hidden" fill="none" stroke="currentColor" strokeWidth={2}>
                <circle cx="5" cy="12" r="1.5" />
                <circle cx="12" cy="12" r="1.5" />
                <circle cx="19" cy="12" r="1.5" />
              </svg>
            </button>
            {moreOpen && (
              <div
                role="menu"
                className="absolute right-0 top-full mt-2 w-64 max-w-[90vw] rounded-xl border border-white/8 bg-ink-900/95 p-1 shadow-2xl backdrop-blur"
              >
                {ECOSYSTEM_LINKS.map((it) => (
                  <a
                    key={it.href}
                    href={it.href}
                    role="menuitem"
                    onClick={() => setMoreOpen(false)}
                    className="block rounded-lg px-3 py-2 text-sm text-ink-200 hover:bg-white/[0.05] hover:text-ink-50"
                  >
                    <div className="font-medium">{it.label}</div>
                    {it.description && (
                      <div className="text-[11px] text-ink-500">{it.description}</div>
                    )}
                  </a>
                ))}
              </div>
            )}
          </div>

          <NavLink to="/pricing" className="hidden hover:text-ink-50 sm:inline">
            Pricing
          </NavLink>
          {me && (
            <NavLink to="/tools" className="hidden hover:text-ink-50 sm:inline">
              Tools
            </NavLink>
          )}

          {me ? (
            <>
              <Link
                to="/chat"
                className="shrink-0 rounded-full bg-accent-600 px-3 py-1.5 text-xs font-medium text-white shadow-glow hover:bg-accent-500 sm:text-sm"
              >
                Open chat
              </Link>
              <UserMenu />
            </>
          ) : (
            <Link
              to="/login"
              className="shrink-0 rounded-full bg-accent-600 px-3 py-1.5 text-xs font-medium text-white shadow-glow hover:bg-accent-500 sm:text-sm"
            >
              Sign in
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
