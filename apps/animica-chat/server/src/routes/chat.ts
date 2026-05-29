// POST /api/chat — open SSE channel, run the agent loop, stream events.
//
// Body shape:
//   {
//     conversationId?: string,    // creates a new one if absent
//     message: string,
//     assistantMode?: string,     // overrides the conversation mode
//     model?: string,             // optional model override per turn
//     approvals?: Record<string,boolean>  // per-tool user approvals
//   }
//
// The response is text/event-stream:
//   event: message     { conversationId, assistantMessageId }
//   event: delta       { text }
//   event: tool_call   { ... }
//   event: tool_result { ... }
//   event: done        { finishReason, usage }
//
// The user message is persisted before the upstream call so a server
// crash mid-turn never loses the prompt. The assistant message is
// persisted on each delta (debounced) so re-opening the conversation
// shows the partial reply.

import { Router } from 'express';
import { z } from 'zod';
import { prisma } from '../prisma';
import { env } from '../env';
import { requireAuth, loadSubscription } from '../middleware/auth';
import { chatRateLimit } from '../middleware/rateLimit';
import { reserveMessage, recordUsage } from '../services/usage';
import { runAgentTurn } from '../services/agentLoop';
import { findMode } from '../services/systemPrompts';

export const chatRouter = Router();

const ChatSchema = z.object({
  conversationId: z.string().optional(),
  message: z.string().min(1),
  assistantMode: z.string().optional(),
  model: z.string().optional(),
  approvals: z.record(z.boolean()).optional(),
});

chatRouter.post('/', chatRateLimit, requireAuth, loadSubscription, async (req, res) => {
  const parsed = ChatSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'bad_request', issues: parsed.error.issues });
    return;
  }
  const { message, conversationId, model } = parsed.data;
  if (message.length > env.MAX_MESSAGE_CHARS) {
    res.status(400).json({ error: 'message_too_long', max: env.MAX_MESSAGE_CHARS });
    return;
  }

  // Reserve usage; abort early if capped.
  const usage = await reserveMessage({
    prisma,
    userId: req.user!.id,
    subscription: req.activeSubscription ?? null,
  });
  if (!usage.ok) {
    res.status(429).json({ error: usage.reason || 'rate_limited' });
    return;
  }

  // Ensure a conversation.
  const assistantMode =
    parsed.data.assistantMode || (await resolveMode(conversationId)) || 'general';
  const mode = findMode(assistantMode) ? assistantMode : 'general';

  const conv = conversationId
    ? await prisma.conversation.findFirst({
        where: { id: conversationId, userId: req.user!.id },
      })
    : await prisma.conversation.create({
        data: { userId: req.user!.id, assistantMode: mode, model: model || null },
      });
  if (!conv) {
    res.status(404).json({ error: 'conversation_not_found' });
    return;
  }
  await prisma.conversation.update({
    where: { id: conv.id },
    data: { assistantMode: mode, model: model || conv.model, updatedAt: new Date() },
  });

  // Persist the user turn.
  await prisma.message.create({
    data: {
      conversationId: conv.id,
      role: 'user',
      content: message,
      isComplete: true,
    },
  });

  // Pre-create the assistant message so streaming has a target.
  const assistantMessage = await prisma.message.create({
    data: {
      conversationId: conv.id,
      role: 'assistant',
      content: '',
      isComplete: false,
      model: model || null,
    },
  });

  // SSE setup.
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no'); // nginx hint
  res.flushHeaders();

  function send(event: string, data: unknown) {
    res.write(`event: ${event}\n`);
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  }

  send('message', {
    conversationId: conv.id,
    assistantMessageId: assistantMessage.id,
  });

  // Wire client-disconnect into an AbortSignal for upstream.
  const abort = new AbortController();
  req.on('close', () => abort.abort());

  let buffered = '';
  let lastFlush = Date.now();
  async function persistPartial(force = false) {
    if (!buffered) return;
    if (!force && Date.now() - lastFlush < 250) return;
    await prisma.message.update({
      where: { id: assistantMessage.id },
      data: { content: buffered },
    });
    lastFlush = Date.now();
  }

  try {
    let promptTokens: number | undefined;
    let completionTokens: number | undefined;
    for await (const evt of runAgentTurn({
      prisma,
      user: req.user!,
      conversationId: conv.id,
      modeCode: mode,
      userMessage: message,
      assistantMessageId: assistantMessage.id,
      approvals: parsed.data.approvals,
      signal: abort.signal,
    })) {
      send(evt.type, evt);
      if (evt.type === 'delta') {
        buffered += evt.text;
        await persistPartial();
      } else if (evt.type === 'done') {
        promptTokens = evt.usage?.promptTokens;
        completionTokens = evt.usage?.completionTokens;
      }
    }
    await persistPartial(true);
    await prisma.message.update({
      where: { id: assistantMessage.id },
      data: {
        isComplete: true,
        promptTokens: promptTokens ?? null,
        completionTokens: completionTokens ?? null,
      },
    });
    await recordUsage({
      prisma,
      userId: req.user!.id,
      promptTokens,
      completionTokens,
    });
    // Auto-title new conversations from the first user message.
    if (conv.title === 'New chat') {
      const draftTitle = message.slice(0, 60).replace(/\s+/g, ' ').trim() || 'New chat';
      await prisma.conversation.update({
        where: { id: conv.id },
        data: { title: draftTitle },
      });
    }
    res.end();
  } catch (err) {
    send('error', { message: err instanceof Error ? err.message : String(err) });
    await persistPartial(true);
    await prisma.message.update({
      where: { id: assistantMessage.id },
      data: { isComplete: true },
    });
    res.end();
  }
});

async function resolveMode(convId?: string): Promise<string | null> {
  if (!convId) return null;
  const c = await prisma.conversation.findUnique({ where: { id: convId } });
  return c?.assistantMode ?? null;
}
