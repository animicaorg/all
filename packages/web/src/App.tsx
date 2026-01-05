import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/authStore'
import Layout from './components/Layout/Layout'

// Pages
import LoginPage from './pages/Auth/LoginPage'
import RegisterPage from './pages/Auth/RegisterPage'
import DashboardPage from './pages/Dashboard/DashboardPage'
import ChatPage from './pages/Chat/ChatPage'
import WorkspacePage from './pages/Workspace/WorkspacePage'
import ModelsPage from './pages/Models/ModelsPage'
import BillingPage from './pages/Billing/BillingPage'
import SettingsPage from './pages/Settings/SettingsPage'
import AdminPage from './pages/Admin/AdminPage'

function App() {
  return (
    <Router>
      <Routes>
        {/* Public routes */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        
        {/* Protected routes */}
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:conversationId" element={<ChatPage />} />
            <Route path="/workspace" element={<WorkspacePage />} />
            <Route path="/workspace/:projectId" element={<WorkspacePage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/billing" element={<BillingPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/admin" element={<AdminPage />} />
          </Route>
        </Route>
      </Routes>
    </Router>
  )
}

// Protected route wrapper
function ProtectedRoute() {
  const { isAuthenticated } = useAuthStore()
  
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }
  
  return <Layout />
}

export default App
