import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

// Global theme + page-local styles
import "../shared/theme.css";
import "./styles.css";

// In MV3 pages we should be resilient to DOM readiness
function mount() {
  const el = document.getElementById("root");
  if (!el) {
    console.error("[onboarding] #root not found");
    return;
  }
  const root = createRoot(el);
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}

// Optional: accept HMR during `pnpm dev` with the MV3 live-reload helper.
// This is no-op in production builds.
if (import.meta && (import.meta as any).hot) {
  (import.meta as any).hot.accept(() => {
    // eslint-disable-next-line no-console
    console.log("[onboarding] HMR update");
  });
}
