import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import UsersPage from './pages/UsersPage';
import BitgoSettingsPage from './pages/BitgoSettingsPage';
import KycPage from './pages/KycPage';
import MarketsPage from './pages/MarketsPage';
import FeesPage from './pages/FeesPage';
import WalletsPage from './pages/WalletsPage';
import WithdrawalsPage from './pages/WithdrawalsPage';
import IncidentsPage from './pages/IncidentsPage';
import AuditPage from './pages/AuditPage';
import Layout from './components/Layout';
import { ErrorPanel } from './components/AdminUI';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-gray-600">Loading...</div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="kyc" element={<KycPage />} />
            <Route path="markets" element={<MarketsPage />} />
            <Route path="fees" element={<FeesPage />} />
            <Route path="wallets" element={<WalletsPage />} />
            <Route path="withdrawals" element={<WithdrawalsPage />} />
            <Route path="incidents" element={<IncidentsPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="settings/bitgo" element={<BitgoSettingsPage />} />
            <Route path="*" element={<ErrorPanel message="Page not found." />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
