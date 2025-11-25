import React from "react";
import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";

type Item = {
  to: string;
  labelKey: string;
  defaultLabel: string;
  icon: React.ReactNode;
  exact?: boolean;
};

const navItems: Item[] = [
  {
    to: "/",
    labelKey: "nav.home",
    defaultLabel: "Home",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M12 3 3 10h2v10h6v-6h2v6h6V10h2z" />
      </svg>
    ),
    exact: true,
  },
  {
    to: "/blocks",
    labelKey: "nav.blocks",
    defaultLabel: "Blocks",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M3 3h8v8H3V3Zm10 0h8v8h-8V3ZM3 13h8v8H3v-8Zm10 0h8v8h-8v-8Z" />
      </svg>
    ),
  },
  {
    to: "/tx",
    labelKey: "nav.transactions",
    defaultLabel: "Transactions",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M7 7h10v2H7V7Zm0 4h10v2H7v-2Zm0 4h10v2H7v-2Z" />
      </svg>
    ),
  },
  {
    to: "/contracts",
    labelKey: "nav.contracts",
    defaultLabel: "Contracts",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M4 4h16v12H5.17L4 17.17V4Zm2 4h12V6H6v2Zm0 4h12v-2H6v2Zm0 4h8v-2H6v2Z" />
      </svg>
    ),
  },
  {
    to: "/aicf",
    labelKey: "nav.aicf",
    defaultLabel: "AI & Quantum",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M12 2 2 7l10 5 10-5-10-5Zm0 7L2 4v13l10 5 10-5V4l-10 5Z" />
      </svg>
    ),
  },
  {
    to: "/da",
    labelKey: "nav.da",
    defaultLabel: "Data Availability",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M12 3C7 3 3 5 3 7.5S7 12 12 12s9-2 9-4.5S17 3 12 3Zm0 6.5C7 9.5 3 11.5 3 14s4 4.5 9 4.5 9-2 9-4.5-4-4.5-9-4.5Zm0 6.5c-5 0-9 2-9 4.5H21c0-2.5-4-4.5-9-4.5Z" />
      </svg>
    ),
  },
  {
    to: "/beacon",
    labelKey: "nav.beacon",
    defaultLabel: "Randomness",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M11 2h2v6h-2V2Zm6.36 3.05 1.41 1.41-4.24 4.24-1.41-1.41 4.24-4.24ZM2 11h6v2H2v-2Zm14.54 1.88 4.24 4.24-1.41 1.41-4.24-4.24 1.41-1.41ZM11 16h2v6h-2v-6ZM4.22 4.46 8.46 8.7 7.05 10.1 2.81 5.86 4.22 4.46Zm-.71 13.66 1.41-1.41 4.24 4.24-1.41 1.41-4.24-4.24Z" />
      </svg>
    ),
  },
  {
    to: "/network",
    labelKey: "nav.network",
    defaultLabel: "Network",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M3 13h8V3H3v10Zm0 8h8v-6H3v6Zm10 0h8v-10h-8v10Zm0-18v6h8V3h-8Z" />
      </svg>
    ),
  },
];

function ItemLink({ to, label, icon, exact }: { to: string; label: string; icon: React.ReactNode; exact?: boolean }) {
  return (
    <NavLink
      to={to}
      end={!!exact}
      className={({ isActive }) =>
        [
          "nav-item",
          isActive ? "active" : "",
        ].join(" ")
      }
      title={label}
      aria-label={label}
    >
      <span className="icon" aria-hidden="true">
        {icon}
      </span>
      <span className="label">{label}</span>
    </NavLink>
  );
}

export default function SideBar() {
  const { t } = useTranslation();

  return (
    <aside className="side-bar" role="navigation" aria-label={t("nav.primary", "Primary")}>
      <div className="brand">
        <span className="dot" />
        <span className="name">Explorer</span>
      </div>

      <nav className="nav-list">
        {navItems.map((n) => (
          <ItemLink
            key={n.to}
            to={n.to}
            exact={n.exact}
            label={(t(n.labelKey, n.defaultLabel) as string) ?? n.defaultLabel}
            icon={n.icon}
          />
        ))}
      </nav>

      <style>{css}</style>
    </aside>
  );
}

// Inline styles keep the component self-contained and production-ready.
// Uses CSS variables defined in the app theme (see styles/*.css).
const css = `
.side-bar{
  position: sticky;
  top: 0;
  height: 100dvh;
  width: 240px;
  min-width: 200px;
  border-right: 1px solid var(--border-muted, #e5e7eb);
  background: var(--bg-elev-0, #ffffff);
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 10px;
}

.brand{
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px 10px 8px;
  font-weight: 700;
  color: var(--text-strong, #111827);
  letter-spacing: .2px;
}
.brand .dot{
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: linear-gradient(135deg, #4f46e5, #22d3ee);
  box-shadow: 0 0 0 2px #22d3ee22;
}
.brand .name{
  font-size: 14px;
  opacity: .9;
}

.nav-list{
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px;
}

.nav-item{
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  color: var(--text, #111827);
  padding: 8px 10px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.2;
  border: 1px solid transparent;
}

.nav-item:hover{
  background: var(--bg-elev-1, #f9fafb);
  border-color: var(--border-muted, #e5e7eb);
}

.nav-item.active{
  background: var(--bg-accent-quiet, #eef2ff);
  color: var(--text-strong, #111827);
  border-color: var(--border-strong, #c7d2fe);
  outline: 2px solid #6366f133;
}

.nav-item .icon{
  display: inline-flex;
  width: 20px;
  height: 20px;
  color: var(--text-muted, #6b7280);
}

.nav-item.active .icon{
  color: var(--accent, #4f46e5);
}

.nav-item .label{
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Compact on narrow layouts */
@media (max-width: 920px) {
  .side-bar{
    width: 64px;
    min-width: 64px;
    padding: 12px 8px;
  }
  .brand .name{ display: none; }
  .nav-item { justify-content: center; padding: 10px 8px; }
  .nav-item .label { display: none; }
}
`;
