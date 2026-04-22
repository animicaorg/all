import { NavLink } from 'react-router-dom';
import type { PropsWithChildren } from 'react';

const navItems = [
  ['/', 'Overview'],
  ['/buy', 'Buy'],
  ['/redeem', 'Redeem'],
  ['/dashboard', 'Dashboard'],
  ['/reserves', 'Reserves'],
  ['/transactions', 'Transactions'],
  ['/compliance', 'Compliance'],
  ['/faq', 'FAQ'],
  ['/support', 'Support'],
  ['/admin', 'Admin']
] as const;

export function NavShell({ children }: PropsWithChildren) {
  return (
    <div className="shell">
      <aside className="sidebar">
        <h1 className="brand">USDAN</h1>
        <p className="subtitle">Animica Dollar platform</p>
        <nav>
          {navItems.map(([path, label]) => (
            <NavLink key={path} to={path} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
