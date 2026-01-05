import { Link, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: '📊' },
  { name: 'Chat', href: '/chat', icon: '💬' },
  { name: 'Workspace', href: '/workspace', icon: '🛠️' },
  { name: 'Models', href: '/models', icon: '🤖' },
  { name: 'Billing', href: '/billing', icon: '💳' },
  { name: 'Settings', href: '/settings', icon: '⚙️' },
]

export default function Sidebar() {
  const location = useLocation()
  const { user, organization } = useAuthStore()
  
  return (
    <div className="flex flex-col w-64 bg-slate-950 border-r border-slate-700">
      {/* Logo */}
      <div className="flex items-center h-16 px-6 border-b border-slate-700">
        <h1 className="text-xl font-bold text-white">
          Animica <span className="text-primary-400">Compute</span>
        </h1>
      </div>
      
      {/* Organization */}
      {organization && (
        <div className="px-4 py-3 border-b border-slate-700">
          <div className="text-sm text-slate-400">Organization</div>
          <div className="text-white font-medium">{organization.name}</div>
          <div className="text-xs text-slate-500 mt-1">
            {organization.credits.toLocaleString()} credits
          </div>
        </div>
      )}
      
      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        {navigation.map((item) => {
          const isActive = location.pathname === item.href || 
            (item.href !== '/' && location.pathname.startsWith(item.href))
          
          return (
            <Link
              key={item.name}
              to={item.href}
              className={`
                flex items-center px-3 py-2 text-sm font-medium rounded-lg
                transition-colors duration-150
                ${isActive
                  ? 'bg-primary-600 text-white'
                  : 'text-slate-300 hover:bg-slate-800 hover:text-white'
                }
              `}
            >
              <span className="mr-3 text-lg">{item.icon}</span>
              {item.name}
            </Link>
          )
        })}
      </nav>
      
      {/* User info */}
      <div className="px-4 py-3 border-t border-slate-700">
        <div className="flex items-center">
          <div className="w-8 h-8 rounded-full bg-primary-600 flex items-center justify-center text-white font-medium">
            {user?.email?.[0]?.toUpperCase() || 'U'}
          </div>
          <div className="ml-3 flex-1 min-w-0">
            <div className="text-sm font-medium text-white truncate">
              {user?.email}
            </div>
            <div className="text-xs text-slate-500 truncate capitalize">
              {user?.role}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
