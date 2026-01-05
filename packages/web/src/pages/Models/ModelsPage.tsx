import { useState, useEffect } from 'react'
import { chatApi } from '@/api/chat'
import type { Model } from '@/types'

export default function ModelsPage() {
  const [models, setModels] = useState<Model[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedModel, setSelectedModel] = useState<Model | null>(null)
  
  useEffect(() => {
    loadModels()
  }, [])
  
  const loadModels = async () => {
    try {
      const data = await chatApi.getModels()
      setModels(data)
    } catch (error) {
      console.error('Failed to load models:', error)
    } finally {
      setLoading(false)
    }
  }
  
  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-white">Loading models...</div>
      </div>
    )
  }
  
  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold text-white mb-2">Available Models</h1>
        <p className="text-slate-400 mb-8">
          Choose from our selection of powerful LLM models
        </p>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {models.map((model) => (
            <ModelCard
              key={model.id}
              model={model}
              selected={selectedModel?.id === model.id}
              onSelect={() => setSelectedModel(model)}
            />
          ))}
          
          {/* Default models if API returns empty */}
          {models.length === 0 && (
            <>
              <ModelCard
                model={{
                  id: 'llama-3-8b',
                  name: 'Llama 3 8B',
                  description: 'Fast and efficient model for general tasks',
                  provider: 'Meta',
                  max_tokens: 8192,
                  cost_per_token: 0.0001,
                  status: 'active',
                }}
                selected={false}
                onSelect={() => {}}
              />
              <ModelCard
                model={{
                  id: 'gpt-4',
                  name: 'GPT-4',
                  description: 'Most capable model for complex reasoning',
                  provider: 'OpenAI',
                  max_tokens: 8192,
                  cost_per_token: 0.03,
                  status: 'active',
                }}
                selected={false}
                onSelect={() => {}}
              />
              <ModelCard
                model={{
                  id: 'claude-3',
                  name: 'Claude 3 Opus',
                  description: 'Excellent for analysis and creative tasks',
                  provider: 'Anthropic',
                  max_tokens: 4096,
                  cost_per_token: 0.015,
                  status: 'active',
                }}
                selected={false}
                onSelect={() => {}}
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ModelCard({ 
  model, 
  selected, 
  onSelect 
}: { 
  model: Model
  selected: boolean
  onSelect: () => void 
}) {
  return (
    <div
      onClick={onSelect}
      className={`
        bg-slate-800 rounded-lg border-2 p-6 cursor-pointer transition-all
        ${selected 
          ? 'border-primary-500 ring-2 ring-primary-500/50' 
          : 'border-slate-700 hover:border-slate-600'
        }
      `}
    >
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="text-xl font-semibold text-white mb-1">{model.name}</h3>
          <span className="text-xs text-slate-500">{model.provider}</span>
        </div>
        <StatusBadge status={model.status} />
      </div>
      
      <p className="text-slate-400 text-sm mb-4">{model.description}</p>
      
      <div className="space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-slate-500">Max tokens:</span>
          <span className="text-white">{model.max_tokens.toLocaleString()}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">Cost per token:</span>
          <span className="text-white">${model.cost_per_token.toFixed(4)}</span>
        </div>
      </div>
      
      <button className="w-full mt-4 py-2 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg transition-colors">
        Use Model
      </button>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const colors = {
    active: 'bg-green-500/20 text-green-400 border-green-500/50',
    inactive: 'bg-slate-500/20 text-slate-400 border-slate-500/50',
    deprecated: 'bg-red-500/20 text-red-400 border-red-500/50',
  }
  
  return (
    <span className={`px-2 py-1 text-xs border rounded-full ${colors[status as keyof typeof colors]}`}>
      {status}
    </span>
  )
}
