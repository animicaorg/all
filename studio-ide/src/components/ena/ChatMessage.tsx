import { useMemo } from "react";
import type { ChatMessage as Msg } from "@/state/chat";

// Very light markdown: paragraphs, inline code, and fenced code blocks.
// Intentionally minimal — no external markdown dep.
function renderLight(text: string): JSX.Element[] {
  const out: JSX.Element[] = [];
  const parts = text.split(/```/);
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      // code block — first line may be a language hint
      const nl = part.indexOf("\n");
      const code = nl >= 0 ? part.slice(nl + 1) : part;
      out.push(
        <pre
          key={`c${i}`}
          className="my-1.5 overflow-x-auto rounded-lg border border-border bg-bg px-3 py-2 text-xs leading-relaxed"
        >
          <code>{code.replace(/\n$/, "")}</code>
        </pre>,
      );
    } else if (part) {
      out.push(
        <span key={`t${i}`} className="whitespace-pre-wrap break-words">
          {renderInline(part)}
        </span>,
      );
    }
  });
  return out;
}

function renderInline(text: string): (string | JSX.Element)[] {
  const nodes: (string | JSX.Element)[] = [];
  const re = /`([^`]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    nodes.push(
      <code key={`ic${k++}`} className="rounded bg-elevated px-1 py-0.5 text-[0.85em] text-accent">
        {m[1]}
      </code>,
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function ChatMessage({ msg }: { msg: Msg }) {
  const body = useMemo(() => renderLight(msg.content), [msg.content]);
  const isUser = msg.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[88%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
          isUser
            ? "bg-accent text-accent-fg"
            : msg.error
              ? "border border-danger/40 bg-danger/10 text-danger"
              : "border border-border bg-surface text-fg"
        }`}
      >
        {msg.content ? (
          <div className={isUser ? "" : "space-y-0.5"}>{body}</div>
        ) : msg.streaming ? (
          <TypingDots />
        ) : (
          <span className="text-muted">…</span>
        )}
        {msg.streaming && msg.content && (
          <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-current align-middle opacity-70" />
        )}
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <span className="flex items-center gap-1 py-0.5">
      <Dot delay="0ms" />
      <Dot delay="150ms" />
      <Dot delay="300ms" />
    </span>
  );
}
function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted"
      style={{ animationDelay: delay }}
    />
  );
}
