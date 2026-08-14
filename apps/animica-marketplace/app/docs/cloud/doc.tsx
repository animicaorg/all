// Shared building blocks for the /docs/cloud pages. Server-safe (no client hooks here) —
// the only client component is the sidebar (nav.tsx), which needs usePathname for the
// active-link highlight.

import type { ReactNode } from 'react';

export const CLOUD_DOC_SECTIONS: { href: string; label: string; group?: string }[] = [
  { href: '/docs/cloud', label: 'Quickstart', group: 'Start' },
  { href: '/docs/cloud/runtime', label: 'Runtime & ABI' },
  { href: '/docs/cloud/packages', label: 'Supported packages' },
  { href: '/docs/cloud/functions', label: 'Functions', group: 'Build' },
  { href: '/docs/cloud/apps', label: 'Apps' },
  { href: '/docs/cloud/agents', label: 'Agents & schedules' },
  { href: '/docs/cloud/ai', label: 'AI' },
  { href: '/docs/cloud/capabilities', label: 'Capabilities & permissions' },
  { href: '/docs/cloud/pricing', label: 'Pricing & economics', group: 'Earn' },
  { href: '/docs/cloud/earnings', label: 'Wallets & earnings' },
  { href: '/docs/cloud/providers', label: 'Compute providers' },
  { href: '/docs/cloud/api', label: 'REST API', group: 'Reference' },
  { href: '/docs/cloud/sdk', label: 'Python SDK' },
  { href: '/docs/cloud/cli', label: 'CLI' },
  { href: '/docs/cloud/security', label: 'Security' },
  { href: '/docs/cloud/examples', label: 'Examples' },
];

export function Code({ children, title }: { children: string; title?: string }) {
  return (
    <div className="cd-codewrap">
      {title ? <div className="cd-codetitle mono">{title}</div> : null}
      <pre className="cd-code">
        <code>{children}</code>
      </pre>
    </div>
  );
}

export function K({ children }: { children: ReactNode }) {
  return <code className="cd-k">{children}</code>;
}

export function Callout({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'warn' }) {
  return (
    <div className="cd-callout" style={tone === 'warn' ? { borderLeftColor: 'var(--warn)' } : undefined}>
      {children}
    </div>
  );
}

export function Table({ head, rows }: { head: ReactNode[]; rows: ReactNode[][] }) {
  return (
    <div className="cd-tablewrap">
      <table className="cd-table">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PageNav({ current }: { current: string }) {
  const idx = CLOUD_DOC_SECTIONS.findIndex((s) => s.href === current);
  const prev = idx > 0 ? CLOUD_DOC_SECTIONS[idx - 1] : null;
  const next = idx >= 0 && idx < CLOUD_DOC_SECTIONS.length - 1 ? CLOUD_DOC_SECTIONS[idx + 1] : null;
  return (
    <div className="cd-pagenav">
      {prev ? (
        <a className="btn ghost" href={prev.href}>
          ← {prev.label}
        </a>
      ) : (
        <span />
      )}
      {next ? (
        <a className="btn ghost" href={next.href}>
          {next.label} →
        </a>
      ) : (
        <span />
      )}
    </div>
  );
}
