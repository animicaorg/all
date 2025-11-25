import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

// Global styles
import "../shared/theme.css";
import "./styles.css";

// Optional: let the background know the popup opened (useful for analytics/metrics)
try {
  chrome.runtime?.sendMessage?.({ type: "popup_opened" });
} catch {
  /* ignore in non-extension contexts */
}

function mount() {
  const el = document.getElementById("root");
  if (!el) {
    console.error("[popup] #root not found");
    return;
  }
  const root = createRoot(el);
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

mount();

// Dev: support Vite HMR during MV3 dev sessions
if (import.meta && (import.meta as any).hot) {
  (import.meta as any).hot.accept(() => {
    // Intentionally empty; Vite will re-run this module and re-render App.
  });
}
