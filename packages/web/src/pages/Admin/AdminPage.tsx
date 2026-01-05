export default function AdminPage() {
  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold text-white mb-2">Admin Dashboard</h1>
        <p className="text-slate-400 mb-8">
          System administration and monitoring
        </p>
        
        {/* System Stats */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
          <StatCard title="Total Users" value="1,247" change="+12%" icon="👥" />
          <StatCard title="Active Sessions" value="89" change="+5%" icon="🔥" />
          <StatCard title="API Requests" value="125K" change="+23%" icon="📡" />
          <StatCard title="GPU Usage" value="87%" change="+8%" icon="⚡" />
        </div>
        
        {/* Recent Activity */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 mb-8">
          <div className="p-6 border-b border-slate-700">
            <h2 className="text-xl font-semibold text-white">Recent Activity</h2>
          </div>
          <div className="divide-y divide-slate-700">
            <ActivityRow
              user="user@example.com"
              action="Created new workspace"
              time="2 min ago"
              status="success"
            />
            <ActivityRow
              user="dev@example.com"
              action="API call failed - rate limit"
              time="5 min ago"
              status="warning"
            />
            <ActivityRow
              user="admin@example.com"
              action="Updated model registry"
              time="15 min ago"
              status="success"
            />
          </div>
        </div>
        
        {/* System Health */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Service Health</h2>
            <div className="space-y-3">
              <ServiceStatus service="API Gateway" status="healthy" />
              <ServiceStatus service="Inference Service" status="healthy" />
              <ServiceStatus service="Auth Service" status="healthy" />
              <ServiceStatus service="Database" status="healthy" />
              <ServiceStatus service="Redis Cache" status="healthy" />
            </div>
          </div>
          
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Resource Usage</h2>
            <div className="space-y-4">
              <ResourceBar label="CPU" percentage={65} />
              <ResourceBar label="Memory" percentage={72} />
              <ResourceBar label="Disk" percentage={45} />
              <ResourceBar label="Network" percentage={38} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function StatCard({ 
  title, 
  value, 
  change, 
  icon 
}: { 
  title: string
  value: string
  change: string
  icon: string
}) {
  const isPositive = change.startsWith('+')
  
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <div className="flex items-center justify-between mb-2">
        <span className="text-2xl">{icon}</span>
        <span className={`text-sm font-medium ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
          {change}
        </span>
      </div>
      <h3 className="text-2xl font-bold text-white mb-1">{value}</h3>
      <p className="text-sm text-slate-400">{title}</p>
    </div>
  )
}

function ActivityRow({ 
  user, 
  action, 
  time, 
  status 
}: { 
  user: string
  action: string
  time: string
  status: 'success' | 'warning' | 'error'
}) {
  const statusColors = {
    success: 'bg-green-500',
    warning: 'bg-yellow-500',
    error: 'bg-red-500',
  }
  
  return (
    <div className="p-4 flex items-center space-x-4 hover:bg-slate-750 transition-colors">
      <div className={`w-2 h-2 rounded-full ${statusColors[status]}`}></div>
      <div className="flex-1 min-w-0">
        <div className="text-white font-medium truncate">{user}</div>
        <div className="text-sm text-slate-400 truncate">{action}</div>
      </div>
      <div className="text-xs text-slate-500">{time}</div>
    </div>
  )
}

function ServiceStatus({ service, status }: { service: string; status: string }) {
  const isHealthy = status === 'healthy'
  
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-300">{service}</span>
      <div className="flex items-center space-x-2">
        <div className={`w-2 h-2 rounded-full ${isHealthy ? 'bg-green-500' : 'bg-red-500'}`}></div>
        <span className={`text-sm ${isHealthy ? 'text-green-400' : 'text-red-400'}`}>
          {status}
        </span>
      </div>
    </div>
  )
}

function ResourceBar({ label, percentage }: { label: string; percentage: number }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-slate-300 text-sm">{label}</span>
        <span className="text-white text-sm font-medium">{percentage}%</span>
      </div>
      <div className="w-full bg-slate-700 rounded-full h-2">
        <div
          className="bg-primary-500 h-2 rounded-full transition-all"
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  )
}
