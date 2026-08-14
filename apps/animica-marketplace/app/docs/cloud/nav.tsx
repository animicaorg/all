'use client';

// Sidebar for /docs/cloud. Client-only for the active-link highlight; deliberately imports
// nothing from lib/ (server-only modules read process.env and throw in the browser).

import { Fragment } from 'react';
import { usePathname } from 'next/navigation';

export default function CloudDocsNav({
  sections,
}: {
  sections: { href: string; label: string; group?: string }[];
}) {
  const pathname = usePathname();
  return (
    <nav className="cd-toc" aria-label="Python Cloud documentation">
      <a className="cd-toc-back" href="/docs">
        ← All docs
      </a>
      {sections.map((s) => (
        <Fragment key={s.href}>
          {s.group ? <span className="cd-toc-grp">{s.group}</span> : null}
          <a href={s.href} className={pathname === s.href ? 'active' : undefined}>
            {s.label}
          </a>
        </Fragment>
      ))}
    </nav>
  );
}
