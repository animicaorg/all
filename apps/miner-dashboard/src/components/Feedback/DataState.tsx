import { ReactNode } from 'react';
import { LoadingSpinner, ErrorMessage } from './LoadingSpinner';

interface DataStateProps {
  isLoading?: boolean;
  isError?: boolean;
  errorMessage?: string;
  children: ReactNode;
  loadingText?: string;
  onRetry?: () => void;
  isEmpty?: boolean;
  emptyMessage?: string;
}

const DataState = ({ 
  isLoading, 
  isError, 
  errorMessage, 
  loadingText = 'Loading…', 
  children,
  onRetry,
  isEmpty,
  emptyMessage = 'No data available'
}: DataStateProps) => {
  if (isLoading) {
    return (
      <div className="glass rounded-2xl p-8 flex justify-center" role="status">
        <LoadingSpinner label={loadingText} />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="glass rounded-2xl p-6" role="alert">
        <ErrorMessage 
          message={errorMessage || 'Unable to reach the pool API'} 
          onRetry={onRetry}
        />
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="glass rounded-2xl p-8 text-center">
        <div className="text-4xl mb-4">📭</div>
        <p className="text-white/60">{emptyMessage}</p>
      </div>
    );
  }

  return <>{children}</>;
};

export default DataState;
