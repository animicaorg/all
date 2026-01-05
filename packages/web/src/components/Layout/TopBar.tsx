import { useAuthStore } from '@/stores/authStore'
import { useNavigate } from 'react-router-dom'

export default function TopBar() {
  const { logout } = useAuthStore()
  const navigate = useNavigate()
  
  const handleLogout = () => {
    logout()
    navigate('/login')
  }
  
  return (
    <div className="flex items-center justify-between h-16 px-6 bg-slate-900 border-b border-slate-700">
      <div className="flex items-center space-x-4">
        {/* Search or breadcrumbs could go here */}
      </div>
      
      <div className="flex items-center space-x-4">
        {/* Notifications */}
        <button className="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800">
          🔔
        </button>
        
        {/* User menu */}
        <button 
          onClick={handleLogout}
          className="px-4 py-2 text-sm text-slate-300 hover:text-white hover:bg-slate-800 rounded-lg"
        >
          Logout
        </button>
      </div>
    </div>
  )
}
