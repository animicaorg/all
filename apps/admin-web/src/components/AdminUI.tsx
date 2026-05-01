import { AlertTriangle, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { type ButtonHTMLAttributes, type ReactNode } from 'react';
import clsx from 'clsx';

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 border-b border-gray-200 pb-5 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold text-gray-950">{title}</h1>
        {description && <p className="mt-1 text-sm text-gray-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx('rounded-lg border border-gray-200 bg-white shadow-sm', className)}>
      {children}
    </section>
  );
}

export function PanelHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-b border-gray-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-700">{title}</h2>
        {description && <p className="mt-1 text-sm text-gray-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Button({
  variant = 'primary',
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
}) {
  return (
    <button
      {...props}
      className={clsx(
        'inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-gray-400 disabled:cursor-not-allowed disabled:opacity-50',
        variant === 'primary' && 'bg-gray-950 text-white hover:bg-gray-800',
        variant === 'secondary' && 'border border-gray-300 bg-white text-gray-800 hover:bg-gray-50',
        variant === 'danger' && 'bg-red-600 text-white hover:bg-red-700',
        variant === 'ghost' && 'text-gray-700 hover:bg-gray-100',
        className
      )}
    >
      {children}
    </button>
  );
}

export function StatusBadge({ value }: { value?: string | boolean | null }) {
  const label = typeof value === 'boolean' ? (value ? 'Enabled' : 'Disabled') : value || 'Unknown';
  const normalized = String(label).toUpperCase();
  const className =
    normalized.includes('ACTIVE') ||
    normalized.includes('ONLINE') ||
    normalized.includes('VERIFIED') ||
    normalized.includes('CONFIRMED') ||
    normalized === 'ENABLED'
      ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
      : normalized.includes('PENDING') ||
          normalized.includes('REQUESTED') ||
          normalized.includes('REVIEW') ||
          normalized.includes('SIGNING') ||
          normalized.includes('BROADCAST') ||
          normalized.includes('IN_PROGRESS') ||
          normalized.includes('READONLY')
        ? 'bg-amber-50 text-amber-700 ring-amber-200'
        : normalized.includes('FAILED') ||
            normalized.includes('REJECTED') ||
            normalized.includes('SUSPENDED') ||
            normalized.includes('HALTED') ||
            normalized.includes('OPEN') ||
            normalized.includes('CRITICAL') ||
            normalized.includes('HIGH')
          ? 'bg-red-50 text-red-700 ring-red-200'
          : 'bg-gray-100 text-gray-700 ring-gray-200';

  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ring-1 ring-inset',
        className
      )}
    >
      {String(label).replace(/_/g, ' ')}
    </span>
  );
}

export function LoadingPanel({ label = 'Loading data' }: { label?: string }) {
  return (
    <Panel className="flex min-h-40 items-center justify-center p-6 text-sm text-gray-500">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      {label}
    </Panel>
  );
}

export function ErrorPanel({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
      <span>{message}</span>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="px-5 py-12 text-center">
      <p className="text-sm font-medium text-gray-900">{title}</p>
      {detail && <p className="mt-1 text-sm text-gray-500">{detail}</p>}
    </div>
  );
}

export function PaginationControls({
  page,
  totalPages,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}) {
  return (
    <div className="flex items-center justify-between border-t border-gray-200 px-5 py-3 text-sm">
      <span className="text-gray-500">
        Page {page} of {Math.max(totalPages, 1)}
      </span>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft className="h-4 w-4" />
          Prev
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-gray-400">None</span>;
  }
  return (
    <pre className="max-h-64 overflow-auto rounded-md bg-gray-950 p-3 text-xs text-gray-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
