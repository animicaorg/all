import { Router } from 'express';
import { z } from 'zod';
import { apiKeyAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createOpenAiRouter(service: PlatformService) {
  const router = Router();

  const chatSchema = z.object({
    model: z.string().min(3),
    project_id: z.string().optional(),
    messages: z.array(
      z.object({
        role: z.enum(['system', 'user', 'assistant']),
        content: z.string().min(1)
      })
    ),
    max_tokens: z.number().int().positive().max(4096).optional(),
    stream: z.boolean().optional(),
    metadata: z.record(z.unknown()).optional()
  });

  const embeddingSchema = z.object({
    model: z.string().min(3),
    project_id: z.string().optional(),
    input: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]),
    dimensions: z.number().int().positive().max(1536).optional()
  });

  router.post('/chat/completions', apiKeyAuth(service, 'inference:chat'), (req, res) => {
    try {
      const body = chatSchema.parse(req.body ?? {});
      const result = service.runChatCompletion({
        project: req.ctx.project!,
        apiKey: req.ctx.apiKey,
        request: {
          model: body.model,
          messages: body.messages,
          max_tokens: body.max_tokens,
          stream: body.stream,
          metadata: body.metadata
        }
      });

      if (body.stream) {
        const content = String(
          ((result.response.choices as Array<{ message: { content: string } }>)?.[0]?.message?.content ?? '')
        );
        const chunks = content.split(/\s+/).filter(Boolean);

        res.setHeader('Content-Type', 'text/event-stream');
        res.setHeader('Cache-Control', 'no-cache');
        res.setHeader('Connection', 'keep-alive');

        chunks.forEach((chunk, idx) => {
          const payload = {
            id: result.response.id,
            object: 'chat.completion.chunk',
            created: result.response.created,
            model: body.model,
            choices: [
              {
                index: 0,
                delta: {
                  content: idx === chunks.length - 1 ? chunk : `${chunk} `
                },
                finish_reason: null
              }
            ]
          };
          res.write(`data: ${JSON.stringify(payload)}\n\n`);
        });

        res.write(
          `data: ${JSON.stringify({
            id: result.response.id,
            object: 'chat.completion.chunk',
            created: result.response.created,
            model: body.model,
            choices: [{ index: 0, delta: {}, finish_reason: 'stop' }]
          })}\n\n`
        );
        res.write('data: [DONE]\n\n');
        res.end();
        return;
      }

      res.status(200).json(result.response);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/embeddings', apiKeyAuth(service, 'inference:embeddings'), (req, res) => {
    try {
      const body = embeddingSchema.parse(req.body ?? {});
      const result = service.runEmbeddings({
        project: req.ctx.project!,
        apiKey: req.ctx.apiKey,
        request: {
          model: body.model,
          input: body.input,
          dimensions: body.dimensions
        }
      });
      res.status(200).json(result.response);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  return router;
}
