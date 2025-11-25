import React, { lazy } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import App from './App';

// Route components (code-split)
const EditPage = lazy(() => import('./pages/Edit/EditPage'));
const DeployPage = lazy(() => import('./pages/Deploy/DeployPage'));
const VerifyPage = lazy(() => import('./pages/Verify/VerifyPage'));
const AIJobsPage = lazy(() => import('./pages/AI/AIJobsPage'));
const QuantumJobsPage = lazy(() => import('./pages/Quantum/QuantumJobsPage'));
const BeaconPage = lazy(() => import('./pages/Randomness/BeaconPage'));
// For DA we reuse the Edit panel as a standalone page for convenience.
const DaPanel = lazy(() => import('./pages/Edit/Panels/DaPanel'));

const NotFound: React.FC = () => (
  <div style={{ padding: 24 }}>
    <h1>404 — Not Found</h1>
    <p>The page you’re looking for doesn’t exist.</p>
    <p>
      Go to <a href="/edit">Edit</a>.
    </p>
  </div>
);

const basename = (import.meta && import.meta.env && import.meta.env.BASE_URL) || '/';

export const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <App />,
      errorElement: (
        <div style={{ padding: 24 }}>
          <h1>Something went wrong</h1>
          <p>Try reloading the page or navigating back.</p>
        </div>
      ),
      children: [
        { index: true, element: <Navigate to="/edit" replace /> },

        // Edit / Simulate workspace
        { path: 'edit', element: <EditPage /> },

        // Deploy / Verify flows
        { path: 'deploy', element: <DeployPage /> },
        { path: 'verify', element: <VerifyPage /> },

        // AI / Quantum job tools
        { path: 'ai', element: <AIJobsPage /> },
        { path: 'quantum', element: <QuantumJobsPage /> },

        // Randomness (Beacon + commit/reveal helpers)
        { path: 'beacon', element: <BeaconPage /> },

        // Data Availability helpers (pin/get/prove)
        { path: 'da', element: <DaPanel /> },

        // Catch-all
        { path: '*', element: <NotFound /> },
      ],
    },
  ],
  { basename }
);

export default router;
