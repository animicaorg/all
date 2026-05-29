import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { sendChat } from '../lib/chatStream';
import { useAuth } from '../lib/auth';
import { ConversationSidebar } from '../components/ConversationSidebar';
import { MessageRow } from '../components/MessageRow';
import { ASSISTANT_MODES, AssistantModePicker } from '../components/AssistantModePicker';
import { ModelTierPicker } from '../components/ModelTierPicker';
import { ToolCallCard, type ToolCallView } from '../components/ToolCallCard';
import { SiteHeader } from '../components/SiteHeader';

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  isComplete: boolean;
  toolCalls?: ToolCallView[];
}

interface FetchedConversation {
  conversation: {
    id: string;
    title: string;
    assistantMode: string;
    messages: Array<{
      id: string;
      role: string;
      content: string;
      isComplete: boolean;
      toolCalls?: any[];
    }>;
  };
}

export function ChatPage() {
  const { conversationId } = useParams();
  const me = useAuth((s) => s.me);
  const nav = useNavigate();
  const [mode, setMode] = useState('general');
  const [tier, setTier] = useState<string>(() => localStorage.getItem('animica-chat:tier') || 'small');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  // Stage label shown to the user while we're between "Send clicked" and
  // "first delta arrived". Updates as the SSE pipeline progresses so the
  // user can see we're not stuck.
  const [streamingLabel, setStreamingLabel] = useState<string>('Thinking');
  const [pendingToolCalls, setPendingToolCalls] = useState<Record<string, ToolCallView>>({});
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [githubLinked, setGithubLinked] = useState<boolean | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Probe whether GitHub is already linked so the "Connect GitHub" CTA
  // only shows when there's actually something to connect.
  useEffect(() => {
    api
      .get<{ github: unknown }>('/api/integrations/status')
      .then((r) => setGithubLinked(Boolean(r.github)))
      .catch(() => setGithubLinked(false));
  }, []);

  // Load conversation history.
  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    (async () => {
      try {
        const r = await api.get<FetchedConversation>(`/api/conversations/${conversationId}`);
        setMode(r.conversation.assistantMode || 'general');
        setMessages(
          r.conversation.messages.map((m) => ({
            id: m.id,
            role: m.role as Message['role'],
            content: m.content,
            isComplete: m.isComplete,
          })),
        );
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          nav('/chat', { replace: true });
        }
      }
    })();
  }, [conversationId, nav]);

  // Auto-scroll while streaming.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streaming]);

  // Keyboard shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        nav('/chat');
      }
      if (e.key === 'Escape' && streaming) {
        abortRef.current?.abort();
      }
      if (e.key === '/' && document.activeElement?.tagName !== 'TEXTAREA' && document.activeElement?.tagName !== 'INPUT') {
        e.preventDefault();
        document.getElementById('chat-input')?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [nav, streaming]);

  async function onSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    if (!input.trim() || streaming) return;
    const userMsg: Message = {
      id: `tmp-user-${Date.now()}`,
      role: 'user',
      content: input,
      isComplete: true,
    };
    const assistantMsg: Message = {
      id: `tmp-asst-${Date.now()}`,
      role: 'assistant',
      content: '',
      isComplete: false,
    };
    setMessages((m) => [...m, userMsg, assistantMsg]);
    setStreaming(true);
    setStreamingLabel('Submitting to chain');
    const message = input;
    setInput('');
    setPendingToolCalls({});

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      let asstId = assistantMsg.id;
      let convId = conversationId;
      await sendChat({
        conversationId,
        message,
        assistantMode: mode,
        model: `animica-chat-${tier}`,
        signal: ac.signal,
        onEvent: (e) => {
          if (e.type === 'message') {
            asstId = e.payload.assistantMessageId;
            convId = e.payload.conversationId;
            setMessages((m) => m.map((x) => (x.id === assistantMsg.id ? { ...x, id: asstId } : x)));
            // Server has accepted the prompt and reserved a slot — next
            // we wait for a miner to claim and serve.
            setStreamingLabel('Waiting for a miner');
          } else if (e.type === 'delta') {
            setStreamingLabel('Generating');
            setMessages((m) =>
              m.map((x) => (x.id === asstId ? { ...x, content: x.content + e.payload.text } : x)),
            );
          } else if (e.type === 'tool_call') {
            setPendingToolCalls((p) => ({
              ...p,
              [e.payload.callId]: {
                callId: e.payload.callId,
                name: e.payload.name,
                args: e.payload.args,
                status: e.payload.status,
              },
            }));
          } else if (e.type === 'tool_result') {
            setPendingToolCalls((p) => ({
              ...p,
              [e.payload.callId]: {
                ...(p[e.payload.callId] ?? { callId: e.payload.callId, name: e.payload.name, args: null, status: 'pending' }),
                status: e.payload.ok ? 'succeeded' : 'failed',
                result: e.payload,
              } as ToolCallView,
            }));
          } else if (e.type === 'done') {
            setMessages((m) => m.map((x) => (x.id === asstId ? { ...x, isComplete: true } : x)));
          } else if (e.type === 'error') {
            setMessages((m) =>
              m.map((x) =>
                x.id === asstId
                  ? { ...x, content: x.content + `\n\n*[error: ${e.payload.message}]*`, isComplete: true }
                  : x,
              ),
            );
          }
        },
      });
      if (convId && convId !== conversationId) {
        nav(`/chat/${convId}`, { replace: true });
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  const pendingToolList = useMemo(() => Object.values(pendingToolCalls), [pendingToolCalls]);
  const empty = messages.length === 0;

  if (me === null) {
    return (
      <div className="grid min-h-full place-items-center bg-ink-950 text-ink-100">
        <Link to="/login" className="rounded-full bg-accent-600 px-6 py-3 text-sm">
          Sign in to chat
        </Link>
      </div>
    );
  }

  return (
    <div className="flex h-[100dvh] flex-col bg-ink-950 text-ink-50">
      <SiteHeader />
      <div className="flex flex-1 overflow-hidden">
        <ConversationSidebar
          open={sidebarOpen}
          onNavigate={() => setSidebarOpen(false)}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-white/5 px-3 py-2 sm:px-6">
            <div className="flex min-w-0 items-center gap-2 sm:gap-3">
              <button
                type="button"
                onClick={() => setSidebarOpen((v) => !v)}
                aria-label="Toggle conversations"
                className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-white/10 bg-white/[0.03] text-ink-200 hover:bg-white/[0.07] md:hidden"
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
              <AssistantModePicker value={mode} onChange={setMode} />
              <ModelTierPicker
                value={tier}
                onChange={(t) => {
                  setTier(t);
                  localStorage.setItem('animica-chat:tier', t);
                }}
              />
              <p className="hidden text-xs text-ink-500 sm:block">
                {ASSISTANT_MODES.find((m) => m.code === mode)?.description}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {githubLinked === false && (
                <a
                  href="/api/integrations/github/start"
                  className="hidden items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-ink-200 hover:bg-white/[0.08] hover:text-ink-50 sm:inline-flex"
                  title="Link your GitHub so the coding agent can read repos and open draft PRs."
                >
                  <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor">
                    <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56v-2.02c-3.2.7-3.88-1.36-3.88-1.36-.52-1.34-1.28-1.69-1.28-1.69-1.05-.72.08-.71.08-.71 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.29 1.18-3.1-.12-.29-.51-1.46.11-3.04 0 0 .97-.31 3.17 1.18.92-.26 1.91-.39 2.89-.39.98 0 1.97.13 2.89.39 2.2-1.49 3.17-1.18 3.17-1.18.62 1.58.23 2.75.11 3.04.74.81 1.18 1.84 1.18 3.1 0 4.43-2.7 5.41-5.27 5.69.41.36.78 1.06.78 2.14v3.18c0 .31.21.68.8.56C20.21 21.38 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
                  </svg>
                  Connect GitHub
                </a>
              )}
              <div className="text-[11px] text-ink-500 sm:text-xs">
                {me?.subscription
                  ? `${me.subscription.messagesUsedThisPeriod}/${me.subscription.weeklyMessages}`
                  : 'Free'}
              </div>
            </div>
          </div>
          <div ref={scrollRef} className="chat-scroll flex-1 overflow-y-auto">
            {empty && (
              <div className="mx-auto mt-24 max-w-xl px-6 text-center">
                <h2 className="text-2xl font-semibold">What can I help you build?</h2>
                <p className="mt-2 text-sm text-ink-400">
                  Pick a mode above, write a prompt, press <kbd className="rounded border border-white/10 px-1">⌘+Enter</kbd> to send.
                </p>
                {githubLinked === false && (
                  <div className="mx-auto mt-6 max-w-md rounded-xl border border-white/8 bg-white/[0.02] p-4 text-left text-sm">
                    <p className="font-medium text-ink-100">Hook up your code</p>
                    <p className="mt-1 text-ink-400">
                      Link GitHub so the <span className="text-ink-200">Coding</span> mode can read your repos and open draft PRs on your behalf.
                    </p>
                    <a
                      href="/api/integrations/github/start"
                      className="mt-3 inline-flex items-center gap-2 rounded-full bg-accent-600 px-4 py-1.5 text-xs font-medium text-white shadow-glow hover:bg-accent-500"
                    >
                      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor">
                        <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56v-2.02c-3.2.7-3.88-1.36-3.88-1.36-.52-1.34-1.28-1.69-1.28-1.69-1.05-.72.08-.71.08-.71 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.29 1.18-3.1-.12-.29-.51-1.46.11-3.04 0 0 .97-.31 3.17 1.18.92-.26 1.91-.39 2.89-.39.98 0 1.97.13 2.89.39 2.2-1.49 3.17-1.18 3.17-1.18.62 1.58.23 2.75.11 3.04.74.81 1.18 1.84 1.18 3.1 0 4.43-2.7 5.41-5.27 5.69.41.36.78 1.06.78 2.14v3.18c0 .31.21.68.8.56C20.21 21.38 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
                      </svg>
                      Connect GitHub
                    </a>
                    <p className="mt-3 text-[11px] text-ink-500">
                      Prefer the terminal? Run <code className="rounded bg-white/5 px-1.5 py-0.5">animica chat</code> and type <code className="rounded bg-white/5 px-1.5 py-0.5">/agent &lt;task&gt;</code> to drive the agent against any local repo.
                    </p>
                  </div>
                )}
              </div>
            )}
            {messages.map((m, i) => {
              const isLatestAssistant =
                streaming && i === messages.length - 1 && m.role === 'assistant';
              return (
                <MessageRow
                  key={m.id}
                  role={m.role}
                  content={m.content}
                  streaming={isLatestAssistant}
                  streamingLabel={isLatestAssistant ? streamingLabel : undefined}
                  onRegenerate={undefined}
                />
              );
            })}
            {pendingToolList.length > 0 && (
              <div className="px-4 sm:px-6">
                {pendingToolList.map((t) => <ToolCallCard key={t.callId} call={t} />)}
              </div>
            )}
          </div>
          <form
            onSubmit={onSubmit}
            className="border-t border-white/5 bg-ink-950 px-2 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] sm:px-6 sm:py-4"
          >
            <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-white/8 bg-white/[0.03] p-2">
              <textarea
                id="chat-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                    e.preventDefault();
                    onSubmit();
                  }
                }}
                placeholder="Ask anything…"
                rows={1}
                className="max-h-60 min-h-[44px] flex-1 resize-none bg-transparent px-3 py-2 text-[15px] placeholder:text-ink-500 focus:outline-none"
              />
              {streaming ? (
                <button
                  type="button"
                  onClick={() => abortRef.current?.abort()}
                  className="rounded-full border border-rose-400/40 bg-rose-500/15 px-4 py-2 text-sm text-rose-200 hover:bg-rose-500/25"
                >
                  Stop
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  className="rounded-full bg-accent-600 px-4 py-2 text-sm font-medium text-white shadow-glow transition hover:bg-accent-500 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-ink-400 disabled:shadow-none"
                >
                  Send
                </button>
              )}
            </div>
            <p className="mx-auto mt-2 max-w-3xl text-center text-[11px] text-ink-500">
              {streaming ? (
                <span className="inline-flex items-center gap-1.5">
                  <span className="inline-flex gap-0.5">
                    <span className="h-1 w-1 animate-bounce rounded-full bg-accent-400 [animation-delay:-200ms]" />
                    <span className="h-1 w-1 animate-bounce rounded-full bg-accent-400 [animation-delay:-100ms]" />
                    <span className="h-1 w-1 animate-bounce rounded-full bg-accent-400" />
                  </span>
                  <span>{streamingLabel}… (Esc to stop)</span>
                </span>
              ) : (
                <>Cmd+Enter to send · Esc to stop · ⌘+K for a new chat · / focuses the box</>
              )}
            </p>
          </form>
        </main>
      </div>
    </div>
  );
}
