import React, { lazy, useEffect } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";

/**
 * NOTE ON CIRCULAR IMPORTS:
 * We intentionally re-declare tiny event helpers here instead of importing
 * them from App.tsx to avoid a circular dependency (App -> router -> App).
 */
const setGlobalLoading = (on: boolean, label?: string) =>
  window.dispatchEvent(new CustomEvent("explorer:loader", { detail: { on, label } }));

// ────────────────────────────────────────────────────────────────────────────────
// Lazy-loaded route components (create these files under src/pages/*)
// ────────────────────────────────────────────────────────────────────────────────
const BlocksPage = lazy(() => import("./pages/Blocks/BlocksPage"));
const TxPage = lazy(() => import("./pages/Tx/TxPage"));
const AddressPage = lazy(() => import("./pages/Address/AddressPage"));
const ContractsPage = lazy(() => import("./pages/Contracts/ContractsPage"));
const AICFPage = lazy(() => import("./pages/AICF/AICFPage"));
const DAPage = lazy(() => import("./pages/DA/DAPage"));
const BeaconPage = lazy(() => import("./pages/Beacon/BeaconPage"));
const NetworkPage = lazy(() => import("./pages/Network/NetworkPage"));
const MarketplacePage = lazy(() => import("./pages/Marketplace/MarketplacePage"));
// Home is optional; we redirect to /blocks by default to keep UX smooth.
// If you later add HomePage, switch the "/" route below.
const HomePage = null;

// ────────────────────────────────────────────────────────────────────────────────
// Public API
// ────────────────────────────────────────────────────────────────────────────────
export default function AppRouter() {
  return (
    <>
      <RouteChangeEffects />
      <Routes>
        {/* Home: redirect to /blocks by default if no HomePage yet */}
        <Route
          path="/"
          element={
            HomePage ? (
              // @ts-expect-error: HomePage is declared null by default
              <HomePage />
            ) : (
              <Navigate to="/blocks" replace />
            )
          }
        />

        {/* Blocks */}
        <Route path="/blocks" element={<BlocksPage />} />
        <Route path="/blocks/:height" element={<BlocksPage />} />

        {/* Transactions */}
        <Route path="/tx" element={<TxPage />} />
        <Route path="/tx/:hash" element={<TxPage />} />

        {/* Addresses */}
        <Route path="/address" element={<AddressPage />} />
        <Route path="/address/:addr" element={<AddressPage />} />

        {/* Contracts */}
        <Route path="/contracts" element={<ContractsPage />} />

        {/* AICF */}
        <Route path="/aicf" element={<AICFPage />} />

        {/* Data Availability */}
        <Route path="/da" element={<DAPage />} />

        {/* Randomness Beacon */}
        <Route path="/beacon" element={<BeaconPage />} />

        {/* Network */}
        <Route path="/network" element={<NetworkPage />} />

        {/* Marketplace */}
        <Route path="/marketplace" element={<MarketplacePage />} />

        {/* 404 */}
        <Route path="*" element={<NotFound />} />
      </Routes>
    </>
  );
}

// ────────────────────────────────────────────────────────────────────────────────
// On route changes: toggle global loader briefly and scroll to top.
// This plays nicely with <Suspense> fallback in App.tsx.
// ────────────────────────────────────────────────────────────────────────────────
function RouteChangeEffects() {
  const { pathname, search, hash } = useLocation();

  useEffect(() => {
    setGlobalLoading(true, "Navigating…");
    const t = window.setTimeout(() => setGlobalLoading(false), 350);
    // Scroll to top unless a deep hash is provided
    if (!hash) window.scrollTo({ top: 0, behavior: "smooth" });
    return () => {
      window.clearTimeout(t);
      // Ensure every navigation start is balanced with a stop event even if the
      // timeout is cleared (e.g., rapid route changes) so the overlay does not
      // get stuck in a pending state.
      setGlobalLoading(false);
    };
  }, [pathname, search, hash]);

  return null;
}

// ────────────────────────────────────────────────────────────────────────────────
// Minimal NotFound page (kept inline so router works without extra files)
// ────────────────────────────────────────────────────────────────────────────────
function NotFound() {
  return (
    <div style={{ padding: "1rem" }}>
      <h1 style={{ margin: "0 0 .25rem 0" }}>404 — Not Found</h1>
      <p className="muted">The page you’re looking for doesn’t exist.</p>
      <p>
        <a className="link" href="/blocks">Go to Blocks</a>
      </p>
    </div>
  );
}
