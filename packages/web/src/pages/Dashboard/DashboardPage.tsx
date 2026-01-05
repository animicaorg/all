import { useAuthStore } from '@/stores/authStore'

export default function DashboardPage() {
  const { user, organization } = useAuthStore()
  
  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">
            Welcome back, {user?.email?.split('@')[0]}!
          </h1>
          <p className="text-slate-400">
            Here's an overview of your account and usage
          </p>
        </div>
        
        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          <StatCard
            title="Credits Remaining"
            value={organization?.credits?.toLocaleString() || '0'}
            icon="💰"
            color="text-green-400"
          />
          <StatCard
            title="Active Projects"
            value="3"
            icon="📁"
            color="text-blue-400"
          />
          <StatCard
            title="API Calls (Today)"
            value="127"
            icon="📊"
            color="text-purple-400"
          />
          <StatCard
            title="Chat Sessions"
            value="8"
            icon="💬"
            color="text-pink-400"
          />
        </div>
        
        {/* Quick Actions */}
        <div className="mb-8">
          <h2 className="text-xl font-semibold text-white mb-4">Quick Actions</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <ActionCard
              title="Start Chat"
              description="Begin a new conversation with AI"
              icon="💬"
              href="/chat"
            />
            <ActionCard
              title="Create Workspace"
              description="Set up a new coding workspace"
              icon="🛠️"
              href="/workspace"
            />
            <ActionCard
              title="View Models"
              description="Explore available LLM models"
              icon="🤖"
              href="/models"
            />
          </div>
        </div>
        
        {/* Recent Activity */}
        <div>
          <h2 className="text-xl font-semibold text-white mb-4">Recent Activity</h2>
          <div className="bg-slate-800 rounded-lg border border-slate-700 divide-y divide-slate-700">
            <ActivityItem
              title="Chat session completed"
              description="Used GPT-4 for code review"
              time="2 hours ago"
              icon="💬"
            />
            <ActivityItem
              title="Workspace deployed"
              description="Project 'ai-assistant' deployed to production"
              time="5 hours ago"
              icon="🚀"
            />
            <ActivityItem
              title="Credits purchased"
              description="Added 10,000 credits to account"
              time="1 day ago"
              icon="💳"
            />
          </div>
        </div>
      </div>
    </div>
  )
}

function StatCard({ title, value, icon, color }: { 
  title: string
  value: string
  icon: string
  color: string 
}) {
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <div className="flex items-center justify-between mb-2">
        <span className="text-2xl">{icon}</span>
        <span className={`text-2xl font-bold ${color}`}>{value}</span>
      </div>
      <h3 className="text-sm text-slate-400">{title}</h3>
    </div>
  )
}

function ActionCard({ title, description, icon, href }: {
  title: string
  description: string
  icon: string
  href: string
}) {
  return (
    <a
      href={href}
      className="block bg-slate-800 hover:bg-slate-750 rounded-lg border border-slate-700 p-6 transition-colors duration-150"
    >
      <div className="text-3xl mb-3">{icon}</div>
      <h3 className="text-lg font-semibold text-white mb-1">{title}</h3>
      <p className="text-sm text-slate-400">{description}</p>
    </a>
  )
}

function ActivityItem({ title, description, time, icon }: {
  title: string
  description: string
  time: string
  icon: string
}) {
  return (
    <div className="p-4 flex items-start space-x-4">
      <span className="text-2xl">{icon}</span>
      <div className="flex-1">
        <h4 className="text-white font-medium">{title}</h4>
        <p className="text-sm text-slate-400">{description}</p>
      </div>
      <span className="text-xs text-slate-500">{time}</span>
    </div>
  )
}
