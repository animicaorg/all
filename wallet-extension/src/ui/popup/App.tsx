import React, { useEffect, useMemo, useState } from "react";
import Home from "./pages/Home";
import Send from "./pages/Send";
import Receive from "./pages/Receive";
import Contracts from "./pages/Contracts";
import Settings from "./pages/Settings";

import BalanceCard from "./components/BalanceCard";
import NetworkSelect from "./components/NetworkSelect";
import AccountSelect from "./components/AccountSelect";

type Tab = "home" | "send" | "receive" | "contracts" | "settings";

function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

const TABS: { key: Tab; label: string; emoji: string }[] = [
  { key: "home", label: "Home", emoji: "🏠" },
  { key: "send", label: "Send", emoji: "📤" },
  { key: "receive", label: "Receive", emoji: "📥" },
  { key: "contracts", label: "Contracts", emoji: "📜" },
  { key: "settings", label: "Settings", emoji: "⚙️" },
];

const STORAGE_KEY = "animica.popup.tab";

export default function App() {
  const [tab, setTab] = useState<Tab>(() => {
    const saved = (localStorage.getItem(STORAGE_KEY) || "") as Tab;
    if (["home", "send", "receive", "contracts", "settings"].includes(saved)) {
      return saved;
    }
    return "home";
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, tab);
    } catch {
      /* ignore */
    }
  }, [tab]);

  // Listen for background pings; keep minimal for now.
  useEffect(() => {
    const handler = (msg: any) => {
      if (!msg || typeof msg !== "object") return;
      // Example: background can nudge the popup to switch tab (e.g., when a draft tx exists)
      if (msg.type === "popup.switchTab" && TABS.some((t) => t.key === msg.to)) {
        setTab(msg.to as Tab);
      }
    };
    try {
      chrome.runtime?.onMessage?.addListener(handler);
      return () => chrome.runtime?.onMessage?.removeListener(handler);
    } catch {
      return () => {};
    }
  }, []);

  const Content = useMemo(() => {
    switch (tab) {
      case "home":
        return (
          <>
            <BalanceCard />
            <Home />
          </>
        );
      case "send":
        return <Send />;
      case "receive":
        return <Receive />;
      case "contracts":
        return <Contracts />;
      case "settings":
        return <Settings />;
      default:
        return <Home />;
    }
  }, [tab]);

  return (
    <div className="ami-popup-root">
      {/* Top bar */}
      <header className="ami-topbar">
        <div className="ami-brand">
          <span className="ami-logo" aria-hidden>⩓</span>
          <span className="ami-title">Animica Wallet</span>
        </div>
        <div className="ami-selects">
          <NetworkSelect />
          <AccountSelect />
        </div>
      </header>

      {/* Main content */}
      <main className="ami-content">{Content}</main>

      {/* Bottom nav */}
      <nav className="ami-bottomnav" aria-label="Primary">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={cx("ami-tab", tab === t.key && "is-active")}
            onClick={() => setTab(t.key)}
            aria-pressed={tab === t.key}
            aria-label={t.label}
            title={t.label}
          >
            <span className="ami-tab-emoji" aria-hidden>
              {t.emoji}
            </span>
            <span className="ami-tab-label">{t.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
