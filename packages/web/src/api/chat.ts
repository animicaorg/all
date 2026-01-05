import { apiClient } from './client'
import type { Conversation, Message, Model } from '@/types'

export interface ChatCompletionRequest {
  model: string
  messages: Array<{ role: string; content: string }>
  temperature?: number
  max_tokens?: number
  stream?: boolean
}

export const chatApi = {
  // Conversations
  getConversations: async (): Promise<Conversation[]> => {
    const response = await apiClient.get('/conversations')
    return response.data
  },
  
  getConversation: async (id: string): Promise<Conversation> => {
    const response = await apiClient.get(`/conversations/${id}`)
    return response.data
  },
  
  createConversation: async (data: { title: string; model: string }): Promise<Conversation> => {
    const response = await apiClient.post('/conversations', data)
    return response.data
  },
  
  updateConversation: async (id: string, data: Partial<Conversation>): Promise<Conversation> => {
    const response = await apiClient.patch(`/conversations/${id}`, data)
    return response.data
  },
  
  deleteConversation: async (id: string): Promise<void> => {
    await apiClient.delete(`/conversations/${id}`)
  },
  
  // Messages
  sendMessage: async (conversationId: string, content: string, model: string): Promise<Message> => {
    const response = await apiClient.post(`/conversations/${conversationId}/messages`, {
      content,
      model,
    })
    return response.data
  },
  
  // Chat completions (streaming)
  streamChatCompletion: async (
    request: ChatCompletionRequest,
    onChunk: (chunk: string) => void,
    onComplete: () => void,
    onError: (error: Error) => void
  ): Promise<void> => {
    try {
      const response = await fetch(`${apiClient.defaults.baseURL}/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: apiClient.defaults.headers.common.Authorization as string,
        },
        body: JSON.stringify({ ...request, stream: true }),
      })
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      
      const reader = response.body?.getReader()
      const decoder = new TextDecoder()
      
      if (!reader) {
        throw new Error('No response body')
      }
      
      while (true) {
        const { done, value } = await reader.read()
        
        if (done) {
          onComplete()
          break
        }
        
        const chunk = decoder.decode(value)
        const lines = chunk.split('\n').filter((line) => line.trim() !== '')
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            
            if (data === '[DONE]') {
              onComplete()
              return
            }
            
            try {
              const parsed = JSON.parse(data)
              const content = parsed.choices?.[0]?.delta?.content
              
              if (content) {
                onChunk(content)
              }
            } catch (e) {
              console.error('Failed to parse SSE data:', e)
            }
          }
        }
      }
    } catch (error) {
      onError(error as Error)
    }
  },
  
  // Models
  getModels: async (): Promise<Model[]> => {
    const response = await apiClient.get('/models')
    return response.data
  },
}
