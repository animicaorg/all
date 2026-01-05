import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useChatStore } from '@/stores/chatStore'
import { chatApi } from '@/api/chat'
import type { Message } from '@/types'

export default function ChatPage() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  
  const {
    conversations,
    activeConversationId,
    isStreaming,
    setActiveConversation,
    addConversation,
    addMessage,
    updateMessage,
    setStreaming,
  } = useChatStore()
  
  const [input, setInput] = useState('')
  const [selectedModel, setSelectedModel] = useState('llama-3-8b-instruct')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  
  const activeConversation = conversations.find((c) => c.id === activeConversationId)
  
  useEffect(() => {
    if (conversationId && conversationId !== activeConversationId) {
      setActiveConversation(conversationId)
    }
  }, [conversationId, activeConversationId, setActiveConversation])
  
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [activeConversation?.messages])
  
  const handleNewConversation = async () => {
    const newConv = await chatApi.createConversation({
      title: 'New Conversation',
      model: selectedModel,
    })
    addConversation(newConv)
    navigate(`/chat/${newConv.id}`)
  }
  
  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (!input.trim() || !activeConversationId || isStreaming) return
    
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      created_at: new Date().toISOString(),
    }
    
    addMessage(activeConversationId, userMessage)
    setInput('')
    setStreaming(true)
    
    const assistantMessageId = (Date.now() + 1).toString()
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
    }
    
    addMessage(activeConversationId, assistantMessage)
    
    const messages = activeConversation?.messages.map((m) => ({
      role: m.role,
      content: m.content,
    })) || []
    
    messages.push({ role: 'user', content: input })
    
    let fullContent = ''
    
    await chatApi.streamChatCompletion(
      {
        model: selectedModel,
        messages,
        temperature: 0.7,
        stream: true,
      },
      (chunk) => {
        fullContent += chunk
        updateMessage(activeConversationId!, assistantMessageId, fullContent)
      },
      () => {
        setStreaming(false)
      },
      (error) => {
        console.error('Stream error:', error)
        setStreaming(false)
        updateMessage(
          activeConversationId!,
          assistantMessageId,
          'Error: Failed to get response'
        )
      }
    )
  }
  
  return (
    <div className="flex h-full">
      {/* Sidebar - Conversation List */}
      <div className="w-64 bg-slate-900 border-r border-slate-700 flex flex-col">
        <div className="p-4 border-b border-slate-700">
          <button
            onClick={handleNewConversation}
            className="w-full py-2 px-4 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg"
          >
            + New Chat
          </button>
        </div>
        
        <div className="flex-1 overflow-y-auto p-2">
          {conversations.map((conv) => (
            <button
              key={conv.id}
              onClick={() => navigate(`/chat/${conv.id}`)}
              className={`
                w-full text-left p-3 rounded-lg mb-2 transition-colors
                ${conv.id === activeConversationId
                  ? 'bg-slate-700 text-white'
                  : 'text-slate-300 hover:bg-slate-800'
                }
              `}
            >
              <div className="font-medium truncate">{conv.title}</div>
              <div className="text-xs text-slate-500 truncate">
                {conv.messages.length} messages
              </div>
            </button>
          ))}
        </div>
      </div>
      
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col">
        {activeConversation ? (
          <>
            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              {activeConversation.messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}
              <div ref={messagesEndRef} />
            </div>
            
            {/* Input */}
            <div className="border-t border-slate-700 p-4">
              <form onSubmit={handleSendMessage} className="flex items-end space-x-4">
                <div className="flex-1">
                  <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault()
                        handleSendMessage(e)
                      }
                    }}
                    placeholder="Type your message..."
                    rows={3}
                    className="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 resize-none"
                    disabled={isStreaming}
                  />
                </div>
                
                <button
                  type="submit"
                  disabled={!input.trim() || isStreaming}
                  className="px-6 py-3 bg-primary-600 hover:bg-primary-700 disabled:bg-slate-700 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
                >
                  {isStreaming ? 'Sending...' : 'Send'}
                </button>
              </form>
              
              <div className="mt-2 flex items-center justify-between text-sm text-slate-500">
                <span>Model: {selectedModel}</span>
                <span>Press Shift+Enter for new line</span>
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-slate-500">
            <div className="text-center">
              <div className="text-6xl mb-4">💬</div>
              <h2 className="text-2xl font-semibold mb-2">Start a conversation</h2>
              <p>Select a chat or create a new one to begin</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`
          max-w-3xl px-4 py-3 rounded-lg
          ${isUser
            ? 'bg-primary-600 text-white'
            : 'bg-slate-700 text-slate-100'
          }
        `}
      >
        <div className="text-xs opacity-70 mb-1">
          {isUser ? 'You' : 'Assistant'}
        </div>
        <div className="whitespace-pre-wrap">{message.content}</div>
      </div>
    </div>
  )
}
