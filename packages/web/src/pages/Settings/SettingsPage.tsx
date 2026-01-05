import { useState } from 'react'
import { useAuthStore } from '@/stores/authStore'

export default function SettingsPage() {
  const { user, organization } = useAuthStore()
  const [activeTab, setActiveTab] = useState('profile')
  
  return (
    <div className="p-8">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-3xl font-bold text-white mb-8">Settings</h1>
        
        {/* Tabs */}
        <div className="flex space-x-4 mb-8 border-b border-slate-700">
          {['profile', 'organization', 'api-keys', 'security'].map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`
                px-4 py-2 font-medium capitalize transition-colors
                ${activeTab === tab
                  ? 'text-primary-400 border-b-2 border-primary-400'
                  : 'text-slate-400 hover:text-white'
                }
              `}
            >
              {tab.replace('-', ' ')}
            </button>
          ))}
        </div>
        
        {/* Tab Content */}
        {activeTab === 'profile' && <ProfileTab user={user} />}
        {activeTab === 'organization' && <OrganizationTab organization={organization} />}
        {activeTab === 'api-keys' && <ApiKeysTab />}
        {activeTab === 'security' && <SecurityTab />}
      </div>
    </div>
  )
}

function ProfileTab({ user }: { user: any }) {
  return (
    <div className="space-y-6">
      <Section title="Profile Information">
        <div className="space-y-4">
          <FormField label="Email" value={user?.email} />
          <FormField label="Wallet Address" value={user?.wallet_address || 'Not connected'} />
          <FormField label="Role" value={user?.role} />
          
          <button className="px-6 py-2 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg">
            Update Profile
          </button>
        </div>
      </Section>
      
      <Section title="Preferences">
        <div className="space-y-4">
          <label className="flex items-center space-x-3 text-slate-300">
            <input type="checkbox" className="rounded" />
            <span>Email notifications</span>
          </label>
          <label className="flex items-center space-x-3 text-slate-300">
            <input type="checkbox" className="rounded" />
            <span>Usage alerts</span>
          </label>
        </div>
      </Section>
    </div>
  )
}

function OrganizationTab({ organization }: { organization: any }) {
  return (
    <div className="space-y-6">
      <Section title="Organization Details">
        <div className="space-y-4">
          <FormField label="Name" value={organization?.name} />
          <FormField label="Slug" value={organization?.slug} />
          <FormField label="Plan" value={organization?.plan} />
          
          <button className="px-6 py-2 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg">
            Update Organization
          </button>
        </div>
      </Section>
      
      <Section title="Team Members">
        <div className="space-y-3">
          <TeamMember
            email="admin@example.com"
            role="Owner"
            avatar="A"
          />
          <TeamMember
            email="dev@example.com"
            role="Member"
            avatar="D"
          />
        </div>
        
        <button className="mt-4 px-6 py-2 bg-slate-700 hover:bg-slate-600 text-white font-medium rounded-lg">
          + Invite Member
        </button>
      </Section>
    </div>
  )
}

function ApiKeysTab() {
  return (
    <div className="space-y-6">
      <Section title="API Keys">
        <p className="text-slate-400 text-sm mb-4">
          API keys allow you to authenticate requests to the Animica Compute API
        </p>
        
        <div className="space-y-3">
          <ApiKeyCard
            name="Production Key"
            prefix="anm_prod_"
            created="2024-01-15"
            lastUsed="2 hours ago"
          />
          <ApiKeyCard
            name="Development Key"
            prefix="anm_dev_"
            created="2024-01-10"
            lastUsed="1 day ago"
          />
        </div>
        
        <button className="mt-4 px-6 py-2 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg">
          + Create API Key
        </button>
      </Section>
    </div>
  )
}

function SecurityTab() {
  return (
    <div className="space-y-6">
      <Section title="Password">
        <div className="space-y-4">
          <FormField label="Current Password" type="password" />
          <FormField label="New Password" type="password" />
          <FormField label="Confirm Password" type="password" />
          
          <button className="px-6 py-2 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg">
            Update Password
          </button>
        </div>
      </Section>
      
      <Section title="Two-Factor Authentication">
        <p className="text-slate-400 text-sm mb-4">
          Add an extra layer of security to your account
        </p>
        <button className="px-6 py-2 bg-slate-700 hover:bg-slate-600 text-white font-medium rounded-lg">
          Enable 2FA
        </button>
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <h2 className="text-xl font-semibold text-white mb-4">{title}</h2>
      {children}
    </div>
  )
}

function FormField({ 
  label, 
  value, 
  type = 'text' 
}: { 
  label: string
  value?: string
  type?: string 
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-300 mb-2">
        {label}
      </label>
      <input
        type={type}
        defaultValue={value}
        className="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
      />
    </div>
  )
}

function TeamMember({ email, role, avatar }: { email: string; role: string; avatar: string }) {
  return (
    <div className="flex items-center justify-between p-3 bg-slate-900 rounded-lg">
      <div className="flex items-center space-x-3">
        <div className="w-10 h-10 bg-primary-600 rounded-full flex items-center justify-center text-white font-medium">
          {avatar}
        </div>
        <div>
          <div className="text-white font-medium">{email}</div>
          <div className="text-sm text-slate-400">{role}</div>
        </div>
      </div>
      <button className="text-sm text-red-400 hover:text-red-300">Remove</button>
    </div>
  )
}

function ApiKeyCard({ 
  name, 
  prefix, 
  created, 
  lastUsed 
}: { 
  name: string
  prefix: string
  created: string
  lastUsed: string
}) {
  return (
    <div className="flex items-center justify-between p-4 bg-slate-900 rounded-lg border border-slate-700">
      <div>
        <div className="text-white font-medium mb-1">{name}</div>
        <div className="text-sm text-slate-400 font-mono">{prefix}••••••••</div>
        <div className="text-xs text-slate-500 mt-1">
          Created: {created} • Last used: {lastUsed}
        </div>
      </div>
      <div className="flex space-x-2">
        <button className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-white text-sm rounded">
          Copy
        </button>
        <button className="px-3 py-1 bg-red-500/10 hover:bg-red-500/20 text-red-400 text-sm rounded">
          Revoke
        </button>
      </div>
    </div>
  )
}
