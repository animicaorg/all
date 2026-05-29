// SSE client for /api/chat.
//
// Parses the server's named-event stream: each event has a name on one
// line ("event: delta") and a JSON payload on the next ("data: {...}"),
// separated by a blank line.

export interface ChatStreamEvent {
  type: string;
  payload: any;
}

export interface SendChatOptions {
  conversationId?: string;
  message: string;
  assistantMode?: string;
  model?: string;
  approvals?: Record<string, boolean>;
  onEvent: (e: ChatStreamEvent) => void;
  signal?: AbortSignal;
}

export async function sendChat(opts: SendChatOptions): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      conversationId: opts.conversationId,
      message: opts.message,
      assistantMode: opts.assistantMode,
      model: opts.model,
      approvals: opts.approvals,
    }),
    signal: opts.signal,
  });
  if (!res.ok || !res.body) {
    let detail = '';
    try { detail = await res.text(); } catch { /* noop */ }
    throw new Error(`chat ${res.status}: ${detail.slice(0, 200)}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf('\n\n')) !== -1) {
      const raw = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let eventName = 'message';
      let data = '';
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (!data) continue;
      try {
        const payload = JSON.parse(data);
        opts.onEvent({ type: eventName, payload });
      } catch {
        /* skip */
      }
    }
  }
}
