import clsx from 'clsx';
import { useState, useCallback } from 'react';
import { MarkdownMessage } from './MarkdownMessage';

export interface MessageRowProps {
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  streaming?: boolean;
  // Short text shown next to the spinner while pre-first-token (e.g.
  // "Submitting to chain", "Worker generating"). Optional — defaults
  // to "Thinking" when not provided.
  streamingLabel?: string;
  onCopy?: () => void;
  onRegenerate?: () => void;
  onEdit?: () => void;
}

function ThinkingIndicator({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-400">
      <span className="inline-flex gap-1">
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-400 [animation-delay:-200ms]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-400 [animation-delay:-100ms]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-400" />
      </span>
      <span>{label}…</span>
    </div>
  );
}

export function MessageRow(props: MessageRowProps) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(props.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
      props.onCopy?.();
    } catch {
      /* noop */
    }
  }, [props]);

  const isUser = props.role === 'user';
  const isAssistant = props.role === 'assistant';
  if (props.role === 'system' || props.role === 'tool') return null;
  return (
    <div
      className={clsx(
        'group flex gap-2.5 px-3 py-4 sm:gap-3 sm:px-6 sm:py-5',
        isUser ? 'bg-transparent' : 'bg-white/[0.015]',
      )}
    >
      <div
        className={clsx(
          'mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md text-xs font-semibold',
          isUser ? 'bg-ink-700 text-ink-100' : 'bg-gradient-to-br from-accent-500 to-accent-700 text-white',
        )}
      >
        {isUser ? 'You' : 'A'}
      </div>
      <div className="min-w-0 flex-1">
        {isAssistant ? (
          props.streaming && !props.content ? (
            // Pre-first-token state: model has accepted the prompt and is
            // working, but no streamed text has arrived yet. The status
            // copy (set by the parent via `streaming`) shows what stage
            // we're in so the user knows we're not stuck.
            <ThinkingIndicator label={props.streamingLabel || 'Thinking'} />
          ) : (
            <MarkdownMessage text={props.content} streaming={props.streaming} />
          )
        ) : (
          <div className="whitespace-pre-wrap text-[15px] leading-relaxed text-ink-100">{props.content}</div>
        )}
        {isAssistant && !props.streaming && (
          <div className="mt-2 flex gap-2 opacity-0 transition-opacity group-hover:opacity-100">
            <button
              type="button"
              onClick={onCopy}
              className="rounded-md border border-white/8 bg-white/5 px-2 py-1 text-xs text-ink-300 hover:text-ink-50"
              aria-label="Copy message"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
            {props.onRegenerate && (
              <button
                type="button"
                onClick={props.onRegenerate}
                className="rounded-md border border-white/8 bg-white/5 px-2 py-1 text-xs text-ink-300 hover:text-ink-50"
              >
                Regenerate
              </button>
            )}
            {props.onEdit && (
              <button
                type="button"
                onClick={props.onEdit}
                className="rounded-md border border-white/8 bg-white/5 px-2 py-1 text-xs text-ink-300 hover:text-ink-50"
              >
                Edit
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
