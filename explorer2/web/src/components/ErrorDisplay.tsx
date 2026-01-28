interface ErrorDisplayProps {
  error: string
  onRetry?: () => void
  className?: string
}

// Helper function to provide user-friendly error context
function getErrorContext(error: string): { title: string; description: string; helpText?: string } {
  const lowerError = error.toLowerCase()
  
  if (lowerError.includes('not support') || lowerError.includes('not available') || lowerError.includes('501')) {
    return {
      title: 'Feature Not Available',
      description: error,
      helpText: 'This node does not support Rich List queries. The node may be running an older version or may not have the required RPC methods enabled.'
    }
  }
  
  if (lowerError.includes('network') || lowerError.includes('connection') || lowerError.includes('fetch')) {
    return {
      title: 'Connection Error',
      description: error,
      helpText: 'Unable to connect to the API server. Please check your network connection and ensure the server is running.'
    }
  }
  
  if (lowerError.includes('timeout')) {
    return {
      title: 'Request Timeout',
      description: error,
      helpText: 'The request took too long to complete. This might happen if the chain has many addresses. Try again in a moment.'
    }
  }
  
  if (lowerError.includes('state db') || lowerError.includes('database')) {
    return {
      title: 'Database Error',
      description: error,
      helpText: 'There was a problem accessing the state database. The node may be syncing or experiencing issues.'
    }
  }
  
  // Default case
  return {
    title: 'Error Loading Data',
    description: error,
    helpText: 'An unexpected error occurred. Please try again or check the API logs for more details.'
  }
}

export default function ErrorDisplay({ error, onRetry, className = '' }: ErrorDisplayProps) {
  const context = getErrorContext(error)
  
  return (
    <div className={`rounded-xl border border-red-200 bg-red-50 p-6 dark:border-red-900/40 dark:bg-red-900/10 ${className}`}>
      <div className="flex items-start gap-3">
        <svg 
          className="mt-0.5 h-6 w-6 flex-shrink-0 text-red-600 dark:text-red-400" 
          fill="none" 
          stroke="currentColor" 
          strokeWidth={2} 
          viewBox="0 0 24 24" 
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
        <div className="flex-1">
          <h3 className="font-semibold text-red-900 dark:text-red-100">{context.title}</h3>
          <p className="mt-2 text-sm text-red-700 dark:text-red-200">{context.description}</p>
          {context.helpText && (
            <p className="mt-2 text-sm text-red-600 dark:text-red-300">{context.helpText}</p>
          )}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-4 inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 dark:bg-red-700 dark:hover:bg-red-800 dark:focus:ring-offset-gray-900"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Try Again
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
