import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from './lib/auth-store';
import LoginPage from './pages/LoginPage';
import MarketsPage from './pages/MarketsPage';
import TradingPage from './pages/TradingPage';
import AccountPage from './pages/AccountPage';
import Layout from './components/Layout';

function App() {
  const { isAuthenticated } = useAuthStore();

  return (
    <Router>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
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
    </Router>
  );
}

export default App;
