import { Navigate, Route, Routes } from 'react-router-dom';
import { NavShell } from './components/NavShell';
import { WalletConnectPanel } from './components/WalletConnectPanel';
import { AdminPage } from './pages/AdminPage';
import { BuyPage } from './pages/BuyPage';
import { CompliancePage } from './pages/CompliancePage';
import { DashboardPage } from './pages/DashboardPage';
import { FaqPage } from './pages/FaqPage';
import { OverviewPage } from './pages/OverviewPage';
import { RedeemPage } from './pages/RedeemPage';
import { ReservesPage } from './pages/ReservesPage';
import { SupportPage } from './pages/SupportPage';
import { TransactionsPage } from './pages/TransactionsPage';

export function App() {
  return (
    <NavShell>
      <WalletConnectPanel />
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/buy" element={<BuyPage />} />
        <Route path="/redeem" element={<RedeemPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/reserves" element={<ReservesPage />} />
        <Route path="/transactions" element={<TransactionsPage />} />
        <Route path="/compliance" element={<CompliancePage />} />
        <Route path="/faq" element={<FaqPage />} />
        <Route path="/support" element={<SupportPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </NavShell>
  );
}
