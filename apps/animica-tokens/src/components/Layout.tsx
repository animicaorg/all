import { NavLink, Outlet } from "react-router-dom";
import { WalletBadge } from "./WalletBadge";

const navItems = [
  ["/", "Overview"],
  ["/launch", "Launch"],
  ["/tokens", "Tokens"],
  ["/dex/swap", "Swap"],
  ["/dex/pools", "Pools"],
  ["/portfolio", "Portfolio"],
  ["/create-pair", "Create Pair"],
  ["/docs", "Docs"],
  ["/admin", "Admin"],
  ["/faq", "FAQ"]
] as const;

export function Layout() {
  return (
    <div className="app-root">
      <header className="topbar">
        <div>
          <h1>Animica Tokens</h1>
          <p>Launcher + DEX</p>
        </div>
        <WalletBadge />
      </header>
      <nav className="nav-grid">
        {navItems.map(([to, label]) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              isActive ? "nav-link nav-link-active" : "nav-link"
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      <main className="main-shell">
        <Outlet />
      </main>
    </div>
  );
}
