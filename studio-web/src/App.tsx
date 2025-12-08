import React, { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';

// These components are provided elsewhere in the repo (see src/components/*)
import TopBar from './components/TopBar';
import SideBar from './components/SideBar';
import StatusBar from './components/StatusBar';
import ToastHost from './components/ToastHost';
import ProviderBanner from './components/ProviderBanner';
import NetworkMismatchBanner from './components/NetworkMismatchBanner';
import { useAccount } from './hooks/useAccount';
import { useNetwork } from './state/network';

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean; err?: unknown }> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError(err: unknown) {
    return { hasError: true, err };
  }
  componentDidCatch(err: unknown, info: unknown) {
    // eslint-disable-next-line no-console
    console.error('UI error caught:', err, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div role="alert" style={{ padding: 16 }}>
          <h1>Something went wrong.</h1>
          <p>Try navigating to a different page or reloading the app.</p>
        </div>
      );
    }
    return this.props.children as React.ReactElement;
  }
}

const App: React.FC = () => {
  const { status, chainId: walletChainId, provider, ensureChain } = useAccount();
  const { chainId: studioChainId } = useNetwork();
  const [providerDismissed, setProviderDismissed] = useState(false);
  const [networkDismissed, setNetworkDismissed] = useState(false);

  const providerStatus = status === "unavailable" ? "unavailable" : status === "connected" || status === "connecting" ? "available" : "unknown";

  const hasNetworkMismatch = walletChainId && studioChainId && walletChainId !== studioChainId;

  const handleSwitchNetwork = async () => {
    if (studioChainId) {
      await ensureChain(studioChainId);
    }
  };

  useEffect(() => {
    // Reset dismissals when conditions change
    if (providerStatus === "available") {
      setProviderDismissed(false);
    }
    if (!hasNetworkMismatch) {
      setNetworkDismissed(false);
    }
  }, [providerStatus, hasNetworkMismatch]);

  return (
    <div className="app-root">
      {/* Skip link for accessibility */}
      <a href="#app-main" className="sr-only focus:not-sr-only focus:outline-none focus:ring">
        Skip to content
      </a>

      <TopBar />

      {/* Provider detection banner */}
      {!providerDismissed && (
        <ProviderBanner
          providerStatus={providerStatus}
          onDismiss={() => setProviderDismissed(true)}
        />
      )}

      {/* Network mismatch banner */}
      {!networkDismissed && hasNetworkMismatch && (
        <NetworkMismatchBanner
          walletChainId={walletChainId!}
          studioChainId={studioChainId!}
          onSwitchNetwork={provider ? handleSwitchNetwork : undefined}
          onDismiss={() => setNetworkDismissed(true)}
        />
      )}

      <div className="app-body">
        <SideBar />

        <main id="app-main" className="app-main" role="main" aria-live="polite">
          <ErrorBoundary>
            <React.Suspense fallback={<div style={{ padding: 16 }}>Loading…</div>}>
              <Outlet />
            </React.Suspense>
          </ErrorBoundary>
        </main>
      </div>

      <StatusBar />

      {/* Toasts render at document end to avoid z-index collisions */}
      <ToastHost />
    </div>
  );
};

export default App;
