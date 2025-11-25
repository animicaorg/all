import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "../shared/theme.css";

export type ApproveKind = "connect" | "sign" | "tx";

export interface InitialApproveState {
  requestId?: string;
  kind: ApproveKind;
  origin?: string;
}

/**
 * Parse query params passed by the background page when opening approve.html:
 * /approve.html?type=connect&req=<id>&origin=<encoded-origin>
 */
function parseInitial(): InitialApproveState {
  const q = new URLSearchParams(window.location.search);
  const kind = (q.get("type") || "connect") as ApproveKind;
  const requestId = q.get("req") || q.get("requestId") || undefined;
  const origin = q.get("origin") || undefined;
  return { kind, requestId, origin };
}

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Missing #root element in approve.html");
}

const initial = parseInitial();

// Best-effort handshake so background knows the window is ready.
try {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (chrome as any)?.runtime?.sendMessage?.({
    type: "approve:opened",
    requestId: initial.requestId,
    kind: initial.kind,
    origin: initial.origin,
  });
} catch {
  // noop (dev/non-extension env)
}

createRoot(rootEl).render(
  <React.StrictMode>
    <App initial={initial} />
  </React.StrictMode>
);

// Convenience: allow ESC to signal a cancel intent back to background.
// Background may close the window upon receiving this message.
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (chrome as any)?.runtime?.sendMessage?.({
        type: "approve:escape",
        requestId: initial.requestId,
      });
    } catch {
      /* ignore */
    }
  }
});

// Resize hint for some MV3 popup window managers (no effect in normal tabs)
function fitContentHeight() {
  // Give the layout a frame to paint before measuring
  requestAnimationFrame(() => {
    const h = document.documentElement.scrollHeight;
    // Some browsers ignore this outside extension popup windows; harmless.
    window.resizeTo(window.outerWidth, Math.max(420, Math.min(h + 24, 900)));
  });
}
window.addEventListener("load", fitContentHeight);
window.addEventListener("resize", fitContentHeight);
