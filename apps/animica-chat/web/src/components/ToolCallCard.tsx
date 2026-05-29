import clsx from 'clsx';

export interface ToolCallView {
  callId: string;
  name: string;
  args: unknown;
  status: 'pending' | 'denied' | 'succeeded' | 'failed';
  result?: { ok?: boolean; summary?: unknown; error?: string; artifactUrl?: string };
}

export function ToolCallCard({ call }: { call: ToolCallView }) {
  return (
    <div className="my-3 rounded-lg border border-white/8 bg-ink-900/40 px-3 py-2 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-mono text-ink-200">{call.name}</span>
        <StatusBadge status={call.status} />
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer text-xs text-ink-400 hover:text-ink-200">arguments</summary>
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-black/30 p-2 text-xs">{JSON.stringify(call.args, null, 2)}</pre>
      </details>
      {call.result && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs text-ink-400 hover:text-ink-200">result</summary>
          <pre className="mt-1 max-h-48 overflow-auto rounded bg-black/30 p-2 text-xs">{JSON.stringify(call.result, null, 2)}</pre>
          {call.result.artifactUrl && (
            <a
              href={call.result.artifactUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-block text-xs text-accent-400 underline-offset-2 hover:underline"
            >
              Open artifact ↗
            </a>
          )}
        </details>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: ToolCallView['status'] }) {
  const map: Record<ToolCallView['status'], string> = {
    pending: 'bg-yellow-500/15 text-yellow-300',
    denied: 'bg-rose-500/15 text-rose-300',
    succeeded: 'bg-emerald-500/15 text-emerald-300',
    failed: 'bg-rose-500/15 text-rose-300',
  };
  return (
    <span className={clsx('rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide', map[status])}>
      {status}
    </span>
  );
}
