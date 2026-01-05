import axios, { AxiosError, AxiosRequestConfig } from 'axios'
import { useAuthStore } from '@/stores/authStore'
import type { ApiError } from '@/types'

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30000,
})

// Request interceptor - add auth token
apiClient.interceptors.request.use(
  (config) => {
    const { tokens } = useAuthStore.getState()
    
    if (tokens?.access_token) {
      config.headers.Authorization = `Bearer ${tokens.access_token}`
    }
    
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor - handle errors
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiError>) => {
    if (error.response?.status === 401) {
      // Token expired, try to refresh
      const { tokens, updateTokens, logout } = useAuthStore.getState()
      
      if (tokens?.refresh_token) {
        try {
          const response = await axios.post(`${API_BASE_URL}/auth/refresh`, {
            refresh_token: tokens.refresh_token,
          })
          
          updateTokens(response.data)
          
          // Retry the original request
          if (error.config) {
            error.config.headers.Authorization = `Bearer ${response.data.access_token}`
            return apiClient.request(error.config)
          }
        } catch (refreshError) {
          // Refresh failed, logout
          logout()
          window.location.href = '/login'
        }
      } else {
        // No refresh token, logout
        logout()
        window.location.href = '/login'
      }
    }
    
    return Promise.reject(error)
  }
)

export const handleApiError = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const apiError = error.response?.data as ApiError
    return apiError?.message || error.message || 'An error occurred'
  }
  
  if (error instanceof Error) {
    return error.message
  }
  
  return 'An unknown error occurred'
}
