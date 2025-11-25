import React from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { router } from './router';

// Global styles
import './styles/tokens.css';
import './styles/theme.css';
import './styles/app.css';

// Guard against missing root element (helps when embedding in other hosts)
const rootEl = document.getElementById('root');
if (!rootEl) {
  throw new Error('Animica Studio: missing <div id="root"></div> in index.html');
}

const RootApp: React.FC = () => {
  return (
    <React.StrictMode>
      {/* The route tree renders <App/> as the shell/layout */}
      <RouterProvider
        router={router}
        fallbackElement={<div style={{ padding: 16 }}>Loading…</div>}
      />
    </React.StrictMode>
  );
};

createRoot(rootEl).render(<RootApp />);
