import { useState } from 'react'
import { useAuthStore } from '@/stores/authStore'

export default function BillingPage() {
  const { organization } = useAuthStore()
  const [selectedPlan, setSelectedPlan] = useState('pro')
  
  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold text-white mb-2">Billing & Usage</h1>
        <p className="text-slate-400 mb-8">
          Manage your subscription and payment methods
        </p>
        
        {/* Current Plan */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white mb-1">
                Current Plan: <span className="text-primary-400 capitalize">{organization?.plan}</span>
              </h2>
              <p className="text-slate-400">
                {organization?.credits.toLocaleString()} credits remaining
              </p>
            </div>
            <button className="px-6 py-2 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg">
              Upgrade Plan
            </button>
          </div>
        </div>
        
        {/* Usage This Month */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <UsageCard
            title="API Calls"
            current={1247}
            limit={10000}
            icon="📡"
          />
          <UsageCard
            title="GPU Hours"
            current={42}
            limit={100}
            icon="⚡"
          />
          <UsageCard
            title="Storage"
            current={3.2}
            limit={10}
            unit="GB"
            icon="💾"
          />
        </div>
        
        {/* Plans */}
        <div className="mb-8">
          <h2 className="text-2xl font-semibold text-white mb-6">Available Plans</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <PlanCard
              name="Starter"
              price={29}
              credits={10000}
              features={[
                '10,000 API calls/month',
                '10 GPU hours',
                '5 GB storage',
                'Email support',
              ]}
              selected={selectedPlan === 'starter'}
              onSelect={() => setSelectedPlan('starter')}
            />
            <PlanCard
              name="Pro"
              price={99}
              credits={50000}
              features={[
                '50,000 API calls/month',
                '50 GPU hours',
                '25 GB storage',
                'Priority support',
                'Advanced models',
              ]}
              selected={selectedPlan === 'pro'}
              onSelect={() => setSelectedPlan('pro')}
              popular
            />
            <PlanCard
              name="Enterprise"
              price={499}
              credits={250000}
              features={[
                'Unlimited API calls',
                '200 GPU hours',
                '100 GB storage',
                '24/7 support',
                'Custom models',
                'SLA guarantee',
              ]}
              selected={selectedPlan === 'enterprise'}
              onSelect={() => setSelectedPlan('enterprise')}
            />
          </div>
        </div>
        
        {/* Payment Methods */}
        <div>
          <h2 className="text-2xl font-semibold text-white mb-6">Payment Methods</h2>
          <div className="space-y-4">
            <PaymentMethodCard
              type="ANM Token"
              details="Connected wallet"
              isDefault
            />
            <PaymentMethodCard
              type="Credit Card"
              details="•••• 4242"
              isDefault={false}
            />
          </div>
          
          <button className="mt-4 px-6 py-2 bg-slate-800 hover:bg-slate-700 text-white font-medium rounded-lg">
            + Add Payment Method
          </button>
        </div>
      </div>
    </div>
  )
}

function UsageCard({ 
  title, 
  current, 
  limit, 
  unit = 'calls',
  icon 
}: { 
  title: string
  current: number
  limit: number
  unit?: string
  icon: string
}) {
  const percentage = (current / limit) * 100
  
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <div className="flex items-center justify-between mb-4">
        <span className="text-2xl">{icon}</span>
        <span className="text-sm text-slate-500">{percentage.toFixed(0)}%</span>
      </div>
      
      <h3 className="text-lg font-semibold text-white mb-2">{title}</h3>
      
      <div className="mb-2">
        <div className="w-full bg-slate-700 rounded-full h-2">
          <div 
            className="bg-primary-500 h-2 rounded-full transition-all"
            style={{ width: `${Math.min(percentage, 100)}%` }}
          />
        </div>
      </div>
      
      <div className="text-sm text-slate-400">
        {current.toLocaleString()} / {limit.toLocaleString()} {unit}
      </div>
    </div>
  )
}

function PlanCard({ 
  name, 
  price, 
  credits,
  features, 
  selected, 
  onSelect,
  popular = false
}: { 
  name: string
  price: number
  credits: number
  features: string[]
  selected: boolean
  onSelect: () => void
  popular?: boolean
}) {
  return (
    <div
      className={`
        relative bg-slate-800 rounded-lg border-2 p-6
        ${selected ? 'border-primary-500' : 'border-slate-700'}
        ${popular ? 'ring-2 ring-primary-500/50' : ''}
      `}
    >
      {popular && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2">
          <span className="px-3 py-1 bg-primary-500 text-white text-xs font-bold rounded-full">
            POPULAR
          </span>
        </div>
      )}
      
      <h3 className="text-2xl font-bold text-white mb-2">{name}</h3>
      <div className="mb-4">
        <span className="text-4xl font-bold text-white">${price}</span>
        <span className="text-slate-400">/month</span>
      </div>
      
      <div className="mb-6 text-sm text-slate-400">
        {credits.toLocaleString()} credits included
      </div>
      
      <ul className="space-y-3 mb-6">
        {features.map((feature, index) => (
          <li key={index} className="flex items-start text-sm text-slate-300">
            <span className="mr-2 text-green-400">✓</span>
            {feature}
          </li>
        ))}
      </ul>
      
      <button
        onClick={onSelect}
        className={`
          w-full py-2 font-medium rounded-lg transition-colors
          ${selected
            ? 'bg-primary-600 hover:bg-primary-700 text-white'
            : 'bg-slate-700 hover:bg-slate-600 text-white'
          }
        `}
      >
        {selected ? 'Current Plan' : 'Select Plan'}
      </button>
    </div>
  )
}

function PaymentMethodCard({ 
  type, 
  details, 
  isDefault 
}: { 
  type: string
  details: string
  isDefault: boolean
}) {
  return (
    <div className="flex items-center justify-between bg-slate-800 rounded-lg border border-slate-700 p-4">
      <div className="flex items-center space-x-4">
        <div className="w-12 h-12 bg-slate-700 rounded-lg flex items-center justify-center">
          {type === 'ANM Token' ? '🪙' : '💳'}
        </div>
        <div>
          <div className="font-medium text-white">{type}</div>
          <div className="text-sm text-slate-400">{details}</div>
        </div>
      </div>
      
      {isDefault && (
        <span className="px-3 py-1 bg-primary-500/20 text-primary-400 text-xs font-medium rounded-full">
          Default
        </span>
      )}
    </div>
  )
}
