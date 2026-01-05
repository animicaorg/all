// User and authentication types
export interface User {
  id: string
  email: string
  wallet_address?: string
  organization_id?: string
  role: 'owner' | 'admin' | 'member'
  created_at: string
  updated_at: string
}

export interface AuthTokens {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface Organization {
  id: string
  name: string
  slug: string
  plan: 'free' | 'starter' | 'pro' | 'enterprise'
  credits: number
  created_at: string
}

// Chat and conversation types
export interface Message {
  id: string
  role: 'system' | 'user' | 'assistant'
  content: string
  created_at: string
  tokens?: number
}

export interface Conversation {
  id: string
  title: string
  model: string
  messages: Message[]
  created_at: string
  updated_at: string
}

// Model types
export interface Model {
  id: string
  name: string
  description: string
  provider: string
  max_tokens: number
  cost_per_token: number
  status: 'active' | 'inactive' | 'deprecated'
}

// Workspace types
export interface Project {
  id: string
  name: string
  description?: string
  repository_url?: string
  github_connected: boolean
  created_at: string
  updated_at: string
}

export interface WorkspaceSession {
  id: string
  project_id: string
  status: 'idle' | 'running' | 'completed' | 'failed'
  files: WorkspaceFile[]
  terminal_output?: string
}

export interface WorkspaceFile {
  path: string
  content: string
  language: string
  modified: boolean
}

// Billing types
export interface UsageRecord {
  id: string
  type: 'chat' | 'code_execution' | 'storage'
  amount: number
  cost: number
  created_at: string
}

export interface Invoice {
  id: string
  amount: number
  status: 'paid' | 'pending' | 'failed'
  due_date: string
  paid_at?: string
  items: InvoiceItem[]
}

export interface InvoiceItem {
  description: string
  quantity: number
  unit_price: number
  total: number
}

export interface PaymentMethod {
  id: string
  type: 'anm' | 'stripe' | 'paypal'
  wallet_address?: string
  card_last4?: string
  is_default: boolean
}

// Agent types
export interface AgentRun {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  goal: string
  repository_url?: string
  branch?: string
  pr_url?: string
  created_at: string
  completed_at?: string
  logs: AgentLog[]
}

export interface AgentLog {
  timestamp: string
  level: 'info' | 'warning' | 'error'
  message: string
}

// API types
export interface ApiKey {
  id: string
  name: string
  key_prefix: string
  created_at: string
  last_used_at?: string
  expires_at?: string
}

// Error types
export interface ApiError {
  code: string
  message: string
  details?: Record<string, any>
}
