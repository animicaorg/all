import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

/**
 * Main entrypoint for the Quantum RNG demo app.
 * Vite injects this script in index.html; we render <App /> into #root.
 * If #root is missing (e.g., when testing standalone), we create it.
 */
function mount() {
  let rootEl = document.getElementById("root");
  if (!rootEl) {
    rootEl = document.createElement("div");
    rootEl.id = "root";
    document.body.appendChild(rootEl);
  }

  const root = createRoot(rootEl);
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

// If the DOM is already ready (usual Vite case), mount immediately.
// Otherwise, wait for DOMContentLoaded to avoid race conditions in tests.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
