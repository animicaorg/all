import { useEffect, useRef } from "react";
import { useChatStore } from "@/state/chat";
import { useEnaStore } from "@/state/ena";
import { ChatMessage } from "@/components/ena/ChatMessage";
import { ChatComposer } from "@/components/ena/ChatComposer";
import { DiffProposalCard } from "@/components/ena/DiffProposalCard";
import { ConnectEna } from "@/components/ena/ConnectEna";

export function EnaPanel() {
  const { messages, sending, proposals, tools, statusPhase, send, stop, resolveProposal } =
    useChatStore();
  const { connected, checked, status } = useEnaStore();
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!checked) void status();
  }, [checked, status]);

  // Autoscroll to bottom on new content.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, proposals, statusPhase, tools.length]);

  // Gate the chat on a connected per-user ENA key.
  if (checked && !connected) return <ConnectEna />;

  const empty = messages.length === 0;
  const activeTools = tools.filter((t) => t.status === "start");

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-none items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-lg bg-accent/15 text-accent">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </span>
          <span className="text-sm font-semibold">ENA</span>
        </div>
        {!empty && (
          <button className="btn-ghost btn-sm" onClick={() => useChatStore.getState().reset()}>
            New chat
          </button>
        )}
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {empty ? (
          <EmptyState onPick={send} />
        ) : (
          <>
            {messages.map((m) => (
              <ChatMessage key={m.id} msg={m} />
            ))}

            {proposals.map((p) => (
              <DiffProposalCard key={p.id} proposal={p} onResolve={resolveProposal} />
            ))}

            {sending && activeTools.length > 0 && (
              <div className="space-y-1">
                {activeTools.map((t, i) => (
                  <ToolRow key={i} name={t.name} summary={t.summary} />
                ))}
              </div>
            )}
            {sending && statusPhase && (
              <div className="px-1 text-xs text-muted">{statusPhase}</div>
            )}
          </>
        )}
        <div ref={bottomRef} />
      </div>

      <ChatComposer sending={sending} onSend={send} onStop={stop} />
    </div>
  );
}

function ToolRow({ name, summary }: { name: string; summary?: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-muted">
      <span className="h-3 w-3 flex-none animate-spin rounded-full border border-border border-t-accent" />
      <span className="font-mono text-fg/80">{name}</span>
      {summary && <span className="truncate">— {summary}</span>}
    </div>
  );
}

const SUGGESTIONS = [
  "Explain what this project does",
  "Find and fix any bugs in the main entry point",
  "Add a README section documenting the setup steps",
];

function EmptyState({ onPick }: { onPick: (s: string) => void }) {
  return (
    <div className="grid h-full place-items-center px-2">
      <div className="w-full max-w-sm text-center">
        <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-accent/15 text-accent">
          <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </div>
        <h2 className="text-base font-semibold">Ask ENA</h2>
        <p className="mt-1 text-sm text-muted">
          ENA reads and edits your repo. You approve every change before it touches a file.
        </p>
        <div className="mt-4 space-y-2 text-left">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              className="card w-full px-3 py-2.5 text-left text-sm hover:bg-elevated"
              onClick={() => onPick(s)}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
