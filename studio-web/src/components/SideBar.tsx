import React, { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

/**
 * SideBar — primary navigation for Studio
 *
 * Routes expected (see router.tsx):
 *  - /edit
 *  - /deploy
 *  - /verify
 *  - /ai
 *  - /quantum
 *  - /da
 *  - /explorer
 *  - /beacon
 */

type Item = {
  to: string;
  label: string;
  icon: React.ReactNode;
  exact?: boolean;
};

const NAV_ITEMS: Item[] = [
  { to: "/edit", label: "Edit & Sim", icon: <Emoji>🧪</Emoji> },
  { to: "/deploy", label: "Deploy", icon: <Emoji>🚀</Emoji> },
  { to: "/verify", label: "Verify", icon: <Emoji>🔍</Emoji> },
  { to: "/ai", label: "AI Jobs", icon: <Emoji>🤖</Emoji> },
  { to: "/quantum", label: "Quantum", icon: <Emoji>⚛️</Emoji> },
  { to: "/da", label: "Data Avail.", icon: <Emoji>🧱</Emoji> },
  { to: "/explorer", label: "Explorer", icon: <Emoji>🗺️</Emoji> },
  { to: "/beacon", label: "Beacon", icon: <Emoji>🔆</Emoji> }
];

function Emoji({ children }: { children: React.ReactNode }) {
  return (
    <span aria-hidden="true" className="emj">
      {children}
      <style>{`
        .emj {
          display: inline-flex;
          width: 1.25rem;
          justify-content: center;
          margin-right: 10px;
        }
      `}</style>
    </span>
  );
}

export default function SideBar() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();

  // Persist collapsed preference
  useEffect(() => {
    const v = localStorage.getItem("studio.sidebar.collapsed");
    if (v === "1") setCollapsed(true);
  }, []);

  // Respect viewport size — collapse on small screens, expand on large unless user opted-in
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia("(max-width: 960px)");
    const sync = (e?: MediaQueryListEvent) => {
      const matches = e ? e.matches : mq.matches;
      if (matches) {
        setCollapsed(true);
      } else {
        const pref = localStorage.getItem("studio.sidebar.collapsed");
        if (pref !== "1") setCollapsed(false);
      }
    };
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  useEffect(() => {
    localStorage.setItem("studio.sidebar.collapsed", collapsed ? "1" : "0");
  }, [collapsed]);

  // Auto-expand on route change for small screens (optional nicety)
  useEffect(() => {
    if (window.matchMedia && window.matchMedia("(max-width: 960px)").matches) {
      setCollapsed(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`} aria-label="Primary">
      <div className="controls">
        <button
          className="collapseBtn"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? "»" : "«"}
        </button>
      </div>

      <nav className="nav">
        {NAV_ITEMS.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.exact}
            className={({ isActive }) => `item ${isActive ? "active" : ""}`}
          >
            {it.icon}
            <span className="label">{it.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="spacer" />

      <div className="footer">
        <a
          className="ext"
          href="https://docs.animica.example/studio"
          target="_blank"
          rel="noreferrer"
          title="Studio docs"
        >
          <Emoji>📘</Emoji>
          <span className="label">Docs</span>
        </a>
        <a
          className="ext"
          href="https://status.animica.example"
          target="_blank"
          rel="noreferrer"
          title="Network status"
        >
          <Emoji>📈</Emoji>
          <span className="label">Status</span>
        </a>
      </div>

      <style>{`
        .sidebar {
          position: sticky;
          top: 56px; /* below TopBar height */
          height: calc(100vh - 56px);
          width: 232px;
          min-width: 232px;
          background: var(--surface);
          border-right: 1px solid var(--border-muted);
          display: flex;
          flex-direction: column;
          transition: width 140ms ease;
        }
        .sidebar.collapsed {
          width: 64px;
          min-width: 64px;
        }
        .controls {
          display: flex;
          justify-content: flex-end;
          padding: 10px 8px 6px 8px;
          border-bottom: 1px solid var(--border-muted);
        }
        .collapseBtn {
          width: 32px;
          height: 28px;
          border: 1px solid var(--border-muted);
          background: var(--surface-elev-1);
          color: var(--fg);
          border-radius: 8px;
          cursor: pointer;
          font-weight: 700;
          line-height: 1;
        }

        .nav {
          padding: 8px;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .item,
        .ext {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 10px 10px;
          border-radius: 10px;
          color: var(--fg);
          text-decoration: none;
          border: 1px solid transparent;
          font-weight: 550;
        }
        .item:hover,
        .ext:hover {
          background: var(--surface-elev-1);
          border-color: var(--border-muted);
        }
        .item.active {
          background: var(--accent-weak);
          border-color: var(--accent);
          color: var(--accent-strong);
        }
        .label {
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .sidebar.collapsed .label {
          display: none;
        }

        .spacer {
          flex: 1 1 auto;
        }

        .footer {
          padding: 8px;
          border-top: 1px solid var(--border-muted);
          display: grid;
          gap: 6px;
        }

        @media (max-width: 960px) {
          .sidebar {
            width: 64px;
            min-width: 64px;
          }
        }
      `}</style>
    </aside>
  );
}
