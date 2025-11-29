import { Routes, Route, Navigate } from 'react-router-dom';

import DashboardPage from './pages/DashboardPage';
import MinersPage from './pages/MinersPage';
import MinerDetailPage from './pages/MinerDetailPage';
import BlocksPage from './pages/BlocksPage';
import SettingsPage from './pages/SettingsPage';
import TopNav from './components/Layout/TopNav';
import Sidebar from './components/Layout/Sidebar';

const App = () => {
  return (
    <div className="min-h-screen text-white">
      <TopNav />
      <div className="flex">
        <Sidebar />
        <main className="flex-1 p-6 sm:p-8 bg-gradient-to-br from-indigo-950/70 via-night to-black/70">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/miners" element={<MinersPage />} />
            <Route path="/miners/:workerId" element={<MinerDetailPage />} />
            <Route path="/blocks" element={<BlocksPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
};

export default App;
