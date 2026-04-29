import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore } from './lib/auth-store';
import { WSProvider } from './components/WSProvider';
import LandingPage from './pages/LandingPage';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import MarketsPage from './pages/MarketsPage';
import TradingPage from './pages/TradingPage';
import AccountPage from './pages/AccountPage';
import LegalPage from './pages/LegalPage';
import Layout from './components/Layout';

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
          <Routes>
            {/* Public routes */}
            <Route path="/" element={!isAuthenticated ? <LandingPage /> : <Navigate to="/markets" replace />} />
            <Route path="/login" element={!isAuthenticated ? <LoginPage /> : <Navigate to="/markets" replace />} />
            <Route path="/register" element={!isAuthenticated ? <RegisterPage /> : <Navigate to="/markets" replace />} />
            <Route path="/legal" element={<LegalPage />} />
            
            {/* Protected routes */}
            <Route
              path="/*"
              element={
                isAuthenticated ? (
                  <Layout>
                    <Routes>
                      <Route path="/markets" element={<MarketsPage />} />
                      <Route path="/trade/:symbol" element={<TradingPage />} />
                      <Route path="/account" element={<AccountPage />} />
                      <Route path="*" element={<Navigate to="/markets" replace />} />
                    </Routes>
                  </Layout>
                ) : (
                  <Navigate to="/login" replace />
                )
              }
            />
          </Routes>
        </WSProvider>
      </Router>
    </QueryClientProvider>
  );
}

export default App;
