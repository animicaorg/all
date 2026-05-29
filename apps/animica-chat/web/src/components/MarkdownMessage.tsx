import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { useCallback, useState } from 'react';

interface Props {
  text: string;
  streaming?: boolean;
}

export function MarkdownMessage({ text, streaming }: Props) {
  return (
    <div className={`prose-chat ${streaming ? 'stream-cursor' : ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function CodeBlock({ children }: { children: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async (e: React.MouseEvent<HTMLButtonElement>) => {
    const pre = e.currentTarget.parentElement;
    const code = pre?.querySelector('code');
    const text = code?.textContent || '';
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* noop */
    }
  }, []);
  return (
    <pre>
      <button
        type="button"
        onClick={onCopy}
        className="absolute right-2 top-2 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-xs text-ink-300 opacity-0 transition group-hover:opacity-100 hover:text-ink-50"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
      {children}
    </pre>
  );
}
