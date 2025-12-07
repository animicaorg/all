interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  label?: string;
}

/**
 * LoadingSpinner for Miner Dashboard
 * Shows an animated spinner with optional label
 */
export function LoadingSpinner({ size = 'md', label }: LoadingSpinnerProps) {
  const sizeClasses = {
    sm: 'h-4 w-4 border-2',
    md: 'h-8 w-8 border-3',
    lg: 'h-12 w-12 border-4',
  };

  return (
    <div className="flex flex-col items-center gap-3">
      <div
        className={`${sizeClasses[size]} border-white/20 border-t-neon rounded-full animate-spin`}
      />
      {label && <p className="text-sm text-white/60">{label}</p>}
    </div>
  );
}

interface LoadingSkeletonProps {
  className?: string;
  count?: number;
}

/**
 * LoadingSkeleton for Miner Dashboard
 * Shows animated placeholder bars
 */
export function LoadingSkeleton({ className = '', count = 1 }: LoadingSkeletonProps) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className={`h-4 bg-gradient-to-r from-white/5 via-white/10 to-white/5 animate-pulse rounded ${className}`}
          style={{
            animationDelay: `${i * 100}ms`,
            animationDuration: '1.5s',
          }}
        />
      ))}
    </>
  );
}

interface ErrorMessageProps {
  message: string;
  onRetry?: () => void;
}

/**
 * ErrorMessage for Miner Dashboard
 * Shows user-friendly error with optional retry
 */
export function ErrorMessage({ message, onRetry }: ErrorMessageProps) {
  return (
    <div className="flex flex-col items-center gap-4 p-6 bg-red-500/10 border border-red-500/30 rounded-lg">
      <div className="text-3xl">❌</div>
      <p className="text-center text-white/80">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-4 py-2 bg-neon text-white font-semibold rounded-lg hover:bg-neon/90 transition-colors"
        >
          Try Again
        </button>
      )}
    </div>
  );
}

export default {
  Spinner: LoadingSpinner,
  Skeleton: LoadingSkeleton,
  Error: ErrorMessage,
};
