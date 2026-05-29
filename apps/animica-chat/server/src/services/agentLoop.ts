// Agent loop with tool calls.
//
// Orchestrates a single "turn" — one user message → potentially many
// model calls (with tool execution in between) → one or more streamed
// assistant deltas. The shape matches OpenAI's tool-calling API: the
// model emits `tool_calls` in its response, the server executes them,
// appends the results as `role:"tool"` messages, and re-asks the model
// until it stops requesting tools or hits a budget.
//
// Two flavours of stream are emitted to the client (Server-Sent Events
// via the chat route):
//   - {type:"delta", text} — incremental assistant text
//   - {type:"tool_call", name, args, status} — UI surfaces a card
//   - {type:"tool_result", name, ok, summary, artifactUrl}
//   - {type:"done", finishReason, usage}

import { PrismaClient, User } from '@prisma/client';
import { AgentToolStatus } from '@prisma/client';
import { env } from '../env';
import { findMode } from './systemPrompts';
import {
  asOpenAITools,
  availableToolsForUser,
  findTool,
  Tool,
} from './toolRegistry';

export type AgentEvent =
  | { type: 'delta'; text: string }
  | {
      type: 'tool_call';
      callId: string;
      name: string;
      args: unknown;
      status: 'pending' | 'denied';
    }
  | {
      type: 'tool_result';
      callId: string;
      name: string;
      ok: boolean;
      summary?: unknown;
      error?: string;
      artifactUrl?: string;
    }
  | { type: 'done'; finishReason?: string; usage?: { promptTokens?: number; completionTokens?: number } };

export interface AgentTurnInput {
  prisma: PrismaClient;
  user: User;
  conversationId: string;
  modeCode: string;
  userMessage: string;
  // Persisted assistant Message we update as we stream.
  assistantMessageId: string;
  // Maximum tool-call rounds before we give up.
  maxToolRounds?: number;
  // Per-call approvals from the UI ({ toolName -> bool }). Read tools
  // bypass approval; write tools require it when policy = "ask".
  approvals?: Record<string, boolean>;
  // AbortSignal so a client disconnect aborts the upstream call.
  signal?: AbortSignal;
}

interface OAIMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | null;
  tool_calls?: Array<{
    id: string;
    type: 'function';
    function: { name: string; arguments: string };
  }>;
  tool_call_id?: string;
  name?: string;
}

interface OAIChoice {
  delta?: {
    content?: string;
    tool_calls?: Array<{
      index: number;
      id?: string;
      type?: 'function';
      function?: { name?: string; arguments?: string };
    }>;
  };
  finish_reason?: string;
}

function shouldExecute(tool: Tool, approvals: Record<string, boolean> | undefined): boolean {
  if (tool.writePolicy === 'read' || tool.writePolicy === 'allow') return true;
  if (tool.writePolicy === 'deny') return false;
  // policy === 'ask'
  return Boolean(approvals?.[tool.name]);
}

function buildSystemMessage(modeCode: string, tools: Tool[]): string {
  const mode = findMode(modeCode);
  const base =
    mode?.defaultPrompt ??
    'You are Animica Chat, a focused AI workspace. Be concise and accurate.';
  if (tools.length === 0) return base;
  const toolDescription = tools.map((t) => `- ${t.name}: ${t.description}`).join('\n');
  return `${base}\n\nYou may call the following tools. Use them when they would materially improve your answer. Never fabricate file contents — read the file first.\n\n${toolDescription}`;
}

export async function* runAgentTurn(input: AgentTurnInput): AsyncGenerator<AgentEvent> {
  const maxRounds = input.maxToolRounds ?? 4;
  const mode = findMode(input.modeCode);
  const allowedTools = await availableToolsForUser({
    prisma: input.prisma,
    user: input.user,
    modeAllowed: mode?.allowedTools ?? [],
  });
  const oaTools = asOpenAITools(allowedTools);

  // Build the message list from the conversation history.
  const history = await input.prisma.message.findMany({
    where: { conversationId: input.conversationId, isComplete: true },
    orderBy: { createdAt: 'asc' },
    take: 50,
  });
  const messages: OAIMessage[] = [
    { role: 'system', content: buildSystemMessage(input.modeCode, allowedTools) },
    ...history.map<OAIMessage>((m) => ({
      role: m.role as OAIMessage['role'],
      content: m.content,
    })),
    { role: 'user', content: input.userMessage },
  ];

  let finalText = '';
  let promptTokens = 0;
  let completionTokens = 0;

  for (let round = 0; round < maxRounds; round++) {
    const url = `${env.AI_BASE_URL}/chat/completions`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.AI_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: mode?.preferredModel || env.AI_DEFAULT_MODEL,
        messages,
        temperature: 0.7,
        max_tokens: env.AI_MAX_TOKENS,
        stream: true,
        ...(oaTools.length > 0 ? { tools: oaTools, tool_choice: 'auto' } : {}),
      }),
      signal: input.signal,
    });
    if (!res.ok || !res.body) {
      const body = await res.text().catch(() => '');
      throw new Error(`ai provider returned ${res.status}: ${body.slice(0, 500)}`);
    }

    let assistantText = '';
    const pendingTools = new Map<
      number,
      { id?: string; name?: string; args: string }
    >();
    let finishReason: string | undefined;

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let usage: { prompt_tokens?: number; completion_tokens?: number } | undefined;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of raw.split('\n')) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const payload = trimmed.slice(5).trim();
          if (payload === '[DONE]') break;
          if (!payload) continue;
          try {
            const j = JSON.parse(payload) as {
              choices?: OAIChoice[];
              usage?: { prompt_tokens?: number; completion_tokens?: number };
            };
            if (j.usage) usage = j.usage;
            const ch = j.choices?.[0];
            if (ch?.finish_reason) finishReason = ch.finish_reason;
            const delta = ch?.delta?.content;
            if (delta) {
              assistantText += delta;
              yield { type: 'delta', text: delta };
            }
            const toolDeltas = ch?.delta?.tool_calls;
            if (toolDeltas) {
              for (const td of toolDeltas) {
                const slot = pendingTools.get(td.index) || { args: '' };
                if (td.id) slot.id = td.id;
                if (td.function?.name) slot.name = td.function.name;
                if (td.function?.arguments) slot.args += td.function.arguments;
                pendingTools.set(td.index, slot);
              }
            }
          } catch {
            /* skip malformed line */
          }
        }
      }
    }

    if (usage?.prompt_tokens) promptTokens += usage.prompt_tokens;
    if (usage?.completion_tokens) completionTokens += usage.completion_tokens;
    finalText = finalText + assistantText;

    // No tool calls → we're done.
    if (pendingTools.size === 0) {
      yield {
        type: 'done',
        finishReason,
        usage: { promptTokens, completionTokens },
      };
      return;
    }

    // Append assistant tool-call turn to history for the next round.
    const toolCallArr = Array.from(pendingTools.entries())
      .sort(([a], [b]) => a - b)
      .map(([_idx, t]) => ({
        id: t.id || `call_${Math.random().toString(36).slice(2)}`,
        type: 'function' as const,
        function: { name: t.name || '', arguments: t.args || '{}' },
      }));
    messages.push({
      role: 'assistant',
      content: assistantText || null,
      tool_calls: toolCallArr,
    });

    // Execute each tool, persist a AgentToolCall row, append a tool
    // result message to the history.
    for (const tc of toolCallArr) {
      const tool = findTool(tc.function.name);
      let args: unknown = {};
      try {
        args = JSON.parse(tc.function.arguments || '{}');
      } catch {
        args = {};
      }
      const callRow = await input.prisma.agentToolCall.create({
        data: {
          messageId: input.assistantMessageId,
          name: tc.function.name,
          argumentsJson: args as object,
          status: AgentToolStatus.PENDING,
        },
      });

      if (!tool) {
        const errMsg = `unknown_tool:${tc.function.name}`;
        await input.prisma.agentToolCall.update({
          where: { id: callRow.id },
          data: {
            status: AgentToolStatus.FAILED,
            outputJson: { error: errMsg },
            completedAt: new Date(),
          },
        });
        messages.push({
          role: 'tool',
          tool_call_id: tc.id,
          name: tc.function.name,
          content: JSON.stringify({ error: errMsg }),
        });
        yield {
          type: 'tool_result',
          callId: callRow.id,
          name: tc.function.name,
          ok: false,
          error: errMsg,
        };
        continue;
      }

      const approved = shouldExecute(tool, input.approvals);
      yield {
        type: 'tool_call',
        callId: callRow.id,
        name: tool.name,
        args,
        status: approved ? 'pending' : 'denied',
      };

      if (!approved) {
        await input.prisma.agentToolCall.update({
          where: { id: callRow.id },
          data: {
            status: AgentToolStatus.DENIED,
            outputJson: { error: 'requires_user_approval' },
            completedAt: new Date(),
          },
        });
        messages.push({
          role: 'tool',
          tool_call_id: tc.id,
          name: tool.name,
          content: JSON.stringify({ error: 'requires_user_approval' }),
        });
        yield {
          type: 'tool_result',
          callId: callRow.id,
          name: tool.name,
          ok: false,
          error: 'requires_user_approval',
        };
        continue;
      }

      const startedAt = Date.now();
      let result;
      try {
        result = await tool.execute(args, {
          prisma: input.prisma,
          user: input.user,
          approved: true,
        });
      } catch (err) {
        result = { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
      const duration = Date.now() - startedAt;
      await input.prisma.agentToolCall.update({
        where: { id: callRow.id },
        data: {
          status: result.ok ? AgentToolStatus.SUCCEEDED : AgentToolStatus.FAILED,
          outputJson: result.data ?? { error: result.error },
          artifactUrl: result.artifactUrl,
          durationMs: duration,
          completedAt: new Date(),
        },
      });

      messages.push({
        role: 'tool',
        tool_call_id: tc.id,
        name: tool.name,
        content: JSON.stringify(result.data ?? { error: result.error ?? 'unknown' }),
      });
      yield {
        type: 'tool_result',
        callId: callRow.id,
        name: tool.name,
        ok: result.ok,
        summary: result.data,
        error: result.error,
        artifactUrl: result.artifactUrl,
      };
    }
    // Loop: another round to let the model respond with the tool result.
  }

  // Hit the round cap; emit a finalising done event so the UI can settle.
  yield {
    type: 'done',
    finishReason: 'tool_round_cap',
    usage: { promptTokens, completionTokens },
  };
}
