import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore } from './lib/auth-store';
import { WSProvider } from './components/WSProvider';
import LoginPage from './pages/LoginPage';
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
  const { isAuthenticated } = useAuthStore();

  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <WSProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/legal" element={<LegalPage />} />
            <Route
              path="/*"
              element={
                isAuthenticated ? (
                  <Layout>
                    <Routes>
                      <Route path="/" element={<Navigate to="/markets" replace />} />
                      <Route path="/markets" element={<MarketsPage />} />
                      <Route path="/trade/:symbol" element={<TradingPage />} />
                      <Route path="/account" element={<AccountPage />} />
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
