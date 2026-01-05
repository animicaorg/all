import { create } from 'zustand'
import type { Conversation, Message } from '@/types'

interface ChatState {
  conversations: Conversation[]
  activeConversationId: string | null
  isStreaming: boolean
  
  // Actions
  setConversations: (conversations: Conversation[]) => void
  addConversation: (conversation: Conversation) => void
  updateConversation: (id: string, updates: Partial<Conversation>) => void
  deleteConversation: (id: string) => void
  setActiveConversation: (id: string | null) => void
  addMessage: (conversationId: string, message: Message) => void
  updateMessage: (conversationId: string, messageId: string, content: string) => void
  setStreaming: (isStreaming: boolean) => void
}

export const useChatStore = create<ChatState>((set) => ({
  conversations: [],
  activeConversationId: null,
  isStreaming: false,
  
  setConversations: (conversations) => set({ conversations }),
  
  addConversation: (conversation) => 
    set((state) => ({ 
      conversations: [conversation, ...state.conversations],
      activeConversationId: conversation.id 
    })),
  
  updateConversation: (id, updates) =>
    set((state) => ({
      conversations: state.conversations.map((conv) =>
        conv.id === id ? { ...conv, ...updates } : conv
      ),
    })),
  
  deleteConversation: (id) =>
    set((state) => ({
      conversations: state.conversations.filter((conv) => conv.id !== id),
      activeConversationId: state.activeConversationId === id ? null : state.activeConversationId,
    })),
  
  setActiveConversation: (id) => set({ activeConversationId: id }),
  
  addMessage: (conversationId, message) =>
    set((state) => ({
      conversations: state.conversations.map((conv) =>
        conv.id === conversationId
          ? { ...conv, messages: [...conv.messages, message] }
          : conv
      ),
    })),
  
  updateMessage: (conversationId, messageId, content) =>
    set((state) => ({
      conversations: state.conversations.map((conv) =>
        conv.id === conversationId
          ? {
              ...conv,
              messages: conv.messages.map((msg) =>
                msg.id === messageId ? { ...msg, content } : msg
              ),
            }
          : conv
      ),
    })),
  
  setStreaming: (isStreaming) => set({ isStreaming }),
}))
