'use client';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { usePathname } from 'next/navigation';

// The interactive half of the site nav. The layout stays a server component and passes the
// link set in; this island renders (a) the desktop link row with a real active state and
// (b) an accessible mobile menu: hamburger with aria-expanded/aria-controls, a full-screen
// panel (portaled to <body> — .nav's backdrop-filter creates a containing block, so a fixed
// child would be clipped to the bar), focus trap, Escape-to-close and body scroll lock.

export interface NavLinkItem {
  href: string;
  label: string;
}

export default function NavClient({ links, cta }: { links: NavLinkItem[]; cta: NavLinkItem }) {
  const path = usePathname() || '/';
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const toggleRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => setMounted(true), []);
  // Close if the route changes under us (client-side navigations).
  useEffect(() => setOpen(false), [path]);

  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const focusables = (): HTMLElement[] =>
      Array.from(panelRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])') ?? []);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
        toggleRef.current?.focus();
        return;
      }
      if (e.key === 'Tab') {
        const els = focusables();
        if (!els.length) return;
        const first = els[0];
        const last = els[els.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey && (active === first || !panelRef.current?.contains(active))) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', onKey);
    // Move focus into the panel.
    const t = window.setTimeout(() => focusables()[0]?.focus(), 0);
    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener('keydown', onKey);
      window.clearTimeout(t);
    };
  }, [open]);

  const isActive = (href: string) => path === href || path.startsWith(href + '/');

  const panel = (
    <div className="mobile-menu" id="site-menu" role="dialog" aria-modal="true" aria-label="Site menu" ref={panelRef}>
      <div className="mobile-menu-head">
        <span style={{ fontWeight: 700, fontSize: 16 }}>Menu</span>
        <button
          className="nav-toggle mm-close"
          aria-label="Close menu"
          onClick={() => {
            setOpen(false);
            toggleRef.current?.focus();
          }}
        >
          ✕
        </button>
      </div>
      <nav aria-label="Site" style={{ display: 'flex', flexDirection: 'column' }}>
        {links.map((l) => (
          <a
            key={l.href}
            href={l.href}
            className={'mm-link' + (isActive(l.href) ? ' active' : '')}
            aria-current={isActive(l.href) ? 'page' : undefined}
            onClick={() => setOpen(false)}
          >
            {l.label}
          </a>
        ))}
      </nav>
      <div style={{ display: 'grid', gap: 10, marginTop: 20 }}>
        <a className="btn primary" style={{ justifyContent: 'center', minHeight: 46 }} href={cta.href}>
          {cta.label}
        </a>
      </div>
    </div>
  );

  return (
    <>
      <div className="nav-links" aria-label="Primary">
        {links.map((l) => (
          <a
            key={l.href}
            href={l.href}
            className={isActive(l.href) ? 'active' : undefined}
            aria-current={isActive(l.href) ? 'page' : undefined}
          >
            {l.label}
          </a>
        ))}
      </div>
      <div className="nav-spacer" />
      <a className="btn primary nav-cta" href={cta.href}>
        {cta.label}
      </a>
      <button
        ref={toggleRef}
        className="nav-toggle"
        aria-label={open ? 'Close menu' : 'Open menu'}
        aria-expanded={open}
        aria-controls="site-menu"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="nav-burger" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      </button>
      {open && mounted ? createPortal(panel, document.body) : null}
    </>
  );
}
