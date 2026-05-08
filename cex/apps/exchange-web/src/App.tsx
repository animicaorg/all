import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Suspense, lazy, useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore } from './lib/auth-store';
import { WSProvider } from './components/WSProvider';
import Layout from './components/Layout';

const LandingPage = lazy(() => import('./pages/LandingPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const RegisterPage = lazy(() => import('./pages/RegisterPage'));
const VerifyEmailPage = lazy(() => import('./pages/VerifyEmailPage'));
const MarketsPage = lazy(() => import('./pages/MarketsPage'));
const TradingPage = lazy(() => import('./pages/TradingPage'));
const AccountPage = lazy(() => import('./pages/AccountPage'));
const AutomationPage = lazy(() => import('./pages/AutomationPage'));
const LegalPage = lazy(() => import('./pages/LegalPage'));
const InfoPage = lazy(() => import('./pages/InfoPage'));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function App() {
  const { authReady, isAuthenticated, initialize } = useAuthStore();

  useEffect(() => {
    void initialize();
  }, [initialize]);

  if (!authReady) {
    return null;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <WSProvider>
          <Suspense fallback={<div className="min-h-screen bg-slate-950" />}>
            <Routes>
              <Route path="/" element={<LandingPage />} />
              <Route path="/login" element={!isAuthenticated ? <LoginPage /> : <Navigate to="/markets" replace />} />
              <Route path="/register" element={!isAuthenticated ? <RegisterPage /> : <Navigate to="/markets" replace />} />
              <Route path="/verify-email" element={<VerifyEmailPage />} />

              <Route
                path="/*"
                element={
                  <Layout>
                    <Routes>
                      <Route path="/markets" element={<MarketsPage />} />
                      <Route path="/trade/:symbol" element={<TradingPage />} />
                      <Route path="/anm" element={<InfoPage />} />
                      <Route path="/anm-markets" element={<InfoPage />} />
                      <Route path="/airdrop" element={<InfoPage />} />
                      <Route path="/fees" element={<InfoPage />} />
                      <Route path="/how-it-works" element={<InfoPage />} />
                      <Route path="/security" element={<InfoPage />} />
                      <Route path="/about" element={<InfoPage />} />
                      <Route path="/legal" element={<LegalPage />} />
                      <Route path="/account" element={isAuthenticated ? <AccountPage /> : <Navigate to="/login" replace />} />
                      <Route path="/automation" element={isAuthenticated ? <AutomationPage /> : <Navigate to="/login" replace />} />
                      <Route path="*" element={<Navigate to="/markets" replace />} />
                    </Routes>
                  </Layout>
                }
              />
            </Routes>
          </Suspense>
        </WSProvider>
      </Router>
    </QueryClientProvider>
  );
}

export default App;
