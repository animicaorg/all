interface SkeletonProps {
  className?: string
}

export default function Skeleton({ className }: SkeletonProps) {
  return (
    <div
      className={`animate-pulse rounded-xl bg-day-200 dark:bg-night-800 ${className ?? ''}`}
      role="status"
      aria-label="Loading..."
    />
  )
}
