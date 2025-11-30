import { ReactNode } from 'react';

interface DataStateProps {
  isLoading?: boolean;
  isError?: boolean;
  errorMessage?: string;
  children: ReactNode;
  loadingText?: string;
}

const DataState = ({ isLoading, isError, errorMessage, loadingText = 'Loading…', children }: DataStateProps) => {
  if (isLoading) {
    return (
      <div className="glass rounded-2xl p-4 text-white/70 text-sm" role="status">
        {loadingText}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="glass rounded-2xl p-4 text-red-200 text-sm" role="alert">
        {errorMessage || 'Unable to reach the pool API'}
      </div>
    );
  }

  return <>{children}</>;
};

export default DataState;
