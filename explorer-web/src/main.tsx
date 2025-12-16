import React, { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

// Global styles
import "./styles/tokens.css";
import "./styles/theme.css";
import "./styles/app.css";
import "./styles/responsive.css";

// Initialize i18n
import "./i18n/config";

/**
 * Apply initial theme before React mounts to avoid FOUC.
 * Reads explicit user preference from localStorage, otherwise falls back to prefers-color-scheme.
 */
(function applyTheme() {
  try {
    const key = "animica-theme";
    const explicit = localStorage.getItem(key) as "light" | "dark" | null;
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const theme = explicit ?? (prefersDark ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", theme);
  } catch {
    // no-op
  }
})();

/** Mount app */
function mount() {
  const containerId = "root";
  let container = document.getElementById(containerId);
  if (!container) {
    container = document.createElement("div");
    container.id = containerId;
    document.body.appendChild(container);
  }

  const root = createRoot(container);
  root.render(
    <StrictMode>
      <App />
    </StrictMode>
  );
}

/** Minimal global error logging (keeps console noise low in production). */
function wireGlobalErrorHandlers() {
  window.addEventListener("error", (e) => {
    // Surface meaningful errors while avoiding logging noise from cancelled network requests, etc.
    if (process.env.NODE_ENV !== "production") {
      // eslint-disable-next-line no-console
      console.error("[explorer] Uncaught error:", e.error ?? e.message);
    }
    // Prevent the error from propagating and crashing the app
    e.preventDefault();
  });
  window.addEventListener("unhandledrejection", (e) => {
    const reason = e.reason;
    
    // Ignore certain benign rejections (network cancellations, etc.)
    const shouldIgnore = 
      reason instanceof DOMException && (
        reason.name === "AbortError" ||
        reason.name === "NetworkError"
      ) ||
      (reason instanceof TypeError && reason.message.includes("fetch"));
    
    if (!shouldIgnore) {
      if (process.env.NODE_ENV !== "production") {
        // eslint-disable-next-line no-console
        console.error("[explorer] Unhandled promise rejection:", reason);
      }
      
      // Show a user-friendly error toast for critical failures
      if (typeof reason === "object" && reason !== null && "message" in reason) {
        try {
          window.dispatchEvent(
            new CustomEvent("explorer:toast", {
              detail: {
                message: `An error occurred: ${reason.message}`,
                kind: "error",
                durationMs: 5000,
              },
            })
          );
        } catch {
          // If toast system fails, fail silently
        }
      }
    }
    
    // Prevent the rejection from propagating
    e.preventDefault();
  });
}

wireGlobalErrorHandlers();
mount();

export {};
