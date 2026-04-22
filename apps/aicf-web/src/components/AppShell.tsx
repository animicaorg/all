import { useEffect, useMemo, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { shortAddress } from '../lib/anm';
import { aicfApi } from '../lib/api';
import { deriveNetworkDemand } from '../lib/gpuEconomics';
import { useSession } from '../lib/session';

const navGroups: Array<{ title: string; items: Array<{ to: string; label: string }> }> = [
  {
    title: 'Developer Workspace',
    items: [
      { to: '/app', label: 'Dashboard' },
      { to: '/app/projects', label: 'Projects' },
      { to: '/app/studio', label: 'Studio + Monaco' },
      { to: '/app/jobs', label: 'Jobs' },
      { to: '/app/contracts', label: 'Contracts' },
      { to: '/app/usage', label: 'Usage + Billing' },
      { to: '/app/wallet', label: 'Wallet' }
    ]
  },
  {
    title: 'Provider Operations',
    items: [
      { to: '/provider', label: 'Provider Dashboard' },
      { to: '/provider/hardware', label: 'GPU Fleet' },
      { to: '/provider/benchmarks', label: 'Benchmarks' },
      { to: '/provider/jobs', label: 'Assignments' },
      { to: '/provider/earnings', label: 'Rewards' },
      { to: '/downloads', label: 'Worker Downloads' }
    ]
  },
  {
    title: 'Network + Docs',
    items: [
      { to: '/', label: 'Home' },
      { to: '/network', label: 'Network Stats' },
      { to: '/status', label: 'Status' },
      { to: '/pricing', label: 'Pricing' },
      { to: '/docs', label: 'Docs' },
      { to: '/app/onboarding', label: 'Wallet Sign-In' }
    ]
  },
  {
    title: 'Admin',
    items: [
      { to: '/admin', label: 'Overview' },
      { to: '/admin/providers', label: 'Provider Ops' },
      { to: '/admin/jobs', label: 'Job Ops' },
      { to: '/admin/disputes', label: 'Disputes' },
      { to: '/admin/treasury', label: 'Treasury' }
    ]
  }
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const { session, setSession } = useSession();
  const [networkStatus, setNetworkStatus] = useState<Record<string, unknown> | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadStatus() {
      try {
        const payload = await aicfApi.status();
        if (!cancelled) {
          setNetworkStatus(payload);
        }
      } catch {
        if (!cancelled) {
          setNetworkStatus(null);
        }
      }
    }

    loadStatus().catch(() => undefined);
    const timer = window.setInterval(() => {
      loadStatus().catch(() => undefined);
    }, 30_000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  const demand = useMemo(() => deriveNetworkDemand(networkStatus), [networkStatus]);

  return (
    <div className="layout-root">
      <header className="topbar">
        <div className="topbar-main">
          <Link to="/" className="brand">
            <span className="brand-badge">AICF</span>
            <span>
              AICF Cloud Console
              <small>Animica GPU layer for AI contracts and workloads</small>
            </span>
          </Link>
          <button
            aria-label="Toggle navigation"
            className="menu-toggle"
            onClick={() => setMenuOpen((prev) => !prev)}
            type="button"
          >
            {menuOpen ? 'Close' : 'Menu'}
          </button>
        </div>

        <div className="topbar-actions">
          <span className="pill">{location.pathname}</span>
          <span className="pill">Demand {demand.demandLabel.toUpperCase()}</span>
          <span className="pill">x{demand.demandMultiplier.toFixed(2)} GPU pricing</span>
          <NavLink to="/app/studio?panel=helper" className="ghost">
            Open Code Helper
          </NavLink>
          {session ? (
            <>
              <span className="pill">
                {session.user.role} · {shortAddress(session.user.wallet?.address ?? session.user.email, 5)}
              </span>
              <button className="ghost" onClick={() => setSession(null)} type="button">
                Sign out
              </button>
            </>
          ) : (
            <NavLink to="/app/onboarding" className="ghost">
              Wallet Sign-In
            </NavLink>
          )}
        </div>
      </header>

      <div className="workspace">
        <aside className={menuOpen ? 'sidebar open' : 'sidebar'}>
          {navGroups.map((group) => (
            <div key={group.title} className="nav-group">
              <h4>{group.title}</h4>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                  end={item.to === '/'}
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </aside>

        <main className="content">{children}</main>
      </div>
    </div>
  );
}
