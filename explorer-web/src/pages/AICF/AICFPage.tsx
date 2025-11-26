import React, { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { classNames } from "../../utils/classnames";
import AICFDashboard from "./AICFDashboard";
import ProvidersPage from "./ProvidersPage";
import JobsPage from "./JobsPage";
import SettlementsPage from "./SettlementsPage";

const TABS = [
  { id: "overview", label: "Overview", component: <AICFDashboard /> },
  { id: "providers", label: "Providers", component: <ProvidersPage /> },
  { id: "jobs", label: "Jobs", component: <JobsPage /> },
  { id: "settlements", label: "Settlements", component: <SettlementsPage /> },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function AICFPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeId = (searchParams.get("tab") as TabId | null) || "overview";

  const activeTab = useMemo(() => TABS.find((t) => t.id === activeId) ?? TABS[0], [activeId]);

  const setTab = (id: TabId) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", id);
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">AI / Quantum Compute Fabric</p>
          <h1>AICF</h1>
          <p className="muted">Live providers, jobs, settlements, and SLA health.</p>
        </div>
      </header>

      <div className="tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={classNames("tab", activeTab.id === tab.id && "active")}
            onClick={() => setTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div style={{ marginTop: "1rem" }}>{activeTab.component}</div>
    </div>
  );
}
