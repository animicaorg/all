#!/usr/bin/env node
/**
 * Animica MCP server.
 *
 * Exposes Animica's OpenAI-compatible AI services as MCP tools so any agent
 * (Claude Desktop, Cursor, Windsurf, custom) can discover and use Animica for
 * cheap inference, code generation, summarization, embeddings, and agent runs.
 *
 * Transports:
 *   - stdio (default)            → local: Claude Desktop / Cursor / Windsurf
 *   - http  (MCP_TRANSPORT=http) → hosted remote MCP on PORT (default 8765)
 *
 * Auth: ANIMICA_API_KEY (get one at https://pool.animica.org).
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { z } from 'zod';
import express from 'express';

import { chat, embed, listModels, gatewayPost, gatewayGet, poolGet, AnimicaError, BASE_URL, POOL_URL, STRATUM_URL } from './client.js';
import { quote, estimateTokens, pricing } from './pricing.js';

const VERSION = '0.1.0';

function text(s: string) { return { content: [{ type: 'text' as const, text: s }] }; }
function json(o: unknown) { return text(JSON.stringify(o, null, 2)); }
function fail(e: unknown) {
  const msg = e instanceof AnimicaError ? e.message : (e as Error)?.message || String(e);
  return { content: [{ type: 'text' as const, text: `⚠️ ${msg}` }], isError: true };
}

export function buildServer(): McpServer {
  const server = new McpServer({ name: 'animica', version: VERSION });

  // --- core inference -------------------------------------------------------
  server.registerTool('animica_chat', {
    title: 'Animica chat',
    description: 'Chat completion via Animica (OpenAI-compatible). Cheap general inference for agents/bots. Pricing: see animica_price_quote. Default model anm-fast-8b.',
    inputSchema: {
      prompt: z.string().optional().describe('A single user prompt (use this OR messages).'),
      messages: z.array(z.object({ role: z.enum(['system', 'user', 'assistant']), content: z.string() })).optional().describe('Full chat history (overrides prompt).'),
      model: z.string().optional().describe('Model id (anm-fast-8b, anm-pro-70b, anm-flagship-moe). Default anm-fast-8b.'),
      max_tokens: z.number().int().positive().max(8192).optional(),
      temperature: z.number().min(0).max(2).optional(),
    },
  }, async ({ prompt, messages, model, max_tokens, temperature }) => {
    try {
      const msgs = messages?.length ? messages : [{ role: 'user' as const, content: prompt || '' }];
      if (!msgs[0]?.content) return fail(new Error('Provide `prompt` or `messages`.'));
      const r = await chat({ messages: msgs, model, max_tokens, temperature });
      return text(r.text);
    } catch (e) { return fail(e); }
  });

  server.registerTool('animica_code', {
    title: 'Animica code generation',
    description: 'Generate or fix code with Animica\'s code model (anm-code-7b). Returns code only. Great for coding agents that need cheap completions.',
    inputSchema: {
      task: z.string().describe('What to build/fix, e.g. "a Python function that parses ISO dates".'),
      language: z.string().optional().describe('Target language (python, typescript, …).'),
      context: z.string().optional().describe('Existing code / surrounding context.'),
      max_tokens: z.number().int().positive().max(8192).optional(),
    },
  }, async ({ task, language, context, max_tokens }) => {
    try {
      const sys = `You are an expert ${language || ''} engineer. Output ONLY code, no prose, no markdown fences unless multiple files. Be correct and idiomatic.`;
      const user = context ? `${task}\n\nExisting context:\n${context}` : task;
      const r = await chat({ model: process.env.ANIMICA_CODE_MODEL || 'anm-code-7b', max_tokens: max_tokens ?? 2048, temperature: 0.2, messages: [{ role: 'system', content: sys }, { role: 'user', content: user }] });
      return text(r.text);
    } catch (e) { return fail(e); }
  });

  server.registerTool('animica_summarize', {
    title: 'Animica summarize',
    description: 'Summarize text with Animica. Cheap bulk summarization for pipelines/bots.',
    inputSchema: {
      text: z.string().describe('Text to summarize.'),
      style: z.enum(['bullets', 'paragraph', 'tldr']).optional(),
      max_words: z.number().int().positive().max(2000).optional(),
    },
  }, async ({ text: body, style, max_words }) => {
    try {
      const instr = `Summarize the following as ${style || 'a concise paragraph'}${max_words ? ` in under ${max_words} words` : ''}. Be faithful; no preamble.`;
      const r = await chat({ messages: [{ role: 'system', content: instr }, { role: 'user', content: body }], temperature: 0.2, max_tokens: 1024 });
      return text(r.text);
    } catch (e) { return fail(e); }
  });

  server.registerTool('animica_embed', {
    title: 'Animica embeddings',
    description: 'Create embedding vectors for text (RAG/search). Returns vectors. Cheapest tier.',
    inputSchema: {
      input: z.union([z.string(), z.array(z.string())]).describe('Text or array of texts to embed.'),
      model: z.string().optional(),
    },
  }, async ({ input, model }) => {
    try { return json(await embed({ input, model })); } catch (e) { return fail(e); }
  });

  // --- agents / apps --------------------------------------------------------
  server.registerTool('animica_agent_run', {
    title: 'Animica agent run',
    description: 'Run an autonomous Animica agent task (tools + multi-step). Falls back to a single reasoning pass if the agent endpoint is unavailable on your gateway.',
    inputSchema: {
      goal: z.string().describe('The task/goal for the agent.'),
      model: z.string().optional(),
      max_steps: z.number().int().positive().max(20).optional(),
    },
  }, async ({ goal, model, max_steps }) => {
    try {
      try {
        const r = await gatewayPost('/agent/run', { goal, model, max_steps: max_steps ?? 8 });
        return json(r);
      } catch (e) {
        if (e instanceof AnimicaError && e.status === 404) {
          const r = await chat({ messages: [{ role: 'system', content: 'You are an autonomous problem-solver. Plan, then produce the final result.' }, { role: 'user', content: goal }], max_tokens: 2048 });
          return text(`(agent endpoint unavailable; single-pass result)\n\n${r.text}`);
        }
        throw e;
      }
    } catch (e) { return fail(e); }
  });

  server.registerTool('animica_generate_app', {
    title: 'Animica generate app',
    description: 'Generate a small runnable app/scaffold from a description (returns a file plan + code).',
    inputSchema: {
      description: z.string().describe('What the app should do.'),
      stack: z.string().optional().describe('Preferred stack (e.g. "FastAPI", "Next.js").'),
    },
  }, async ({ description, stack }) => {
    try {
      const r = await chat({ model: process.env.ANIMICA_CODE_MODEL || 'anm-code-7b', max_tokens: 4096, temperature: 0.3, messages: [
        { role: 'system', content: 'You are a senior engineer. Output a minimal runnable app as a list of files, each as `### path` followed by a fenced code block. Include a README and a run command. No extra prose.' },
        { role: 'user', content: `Build: ${description}${stack ? `\nStack: ${stack}` : ''}` },
      ] });
      return text(r.text);
    } catch (e) { return fail(e); }
  });

  // --- discovery / commerce -------------------------------------------------
  server.registerTool('animica_search_docs', {
    title: 'Animica docs search',
    description: 'Answer a question about Animica (API, models, pricing, MCP) with links. Use before integrating.',
    inputSchema: { query: z.string().describe('Your question about Animica.') },
  }, async ({ query }) => {
    try {
      const ctx = 'Animica is an OpenAI-compatible AI API at https://pool.animica.org/v1 (chat/completions, models, embeddings). Get an API key at https://pool.animica.org. MCP server: animica-mcp. Docs: https://animica.org/developers. Models: anm-fast-8b (general), anm-code-7b (code), anm-pro-70b, anm-flagship-moe (MoE). Billing: prepaid credits + pay-as-you-go, crypto via NOWPayments.';
      const r = await chat({ messages: [{ role: 'system', content: `Answer ONLY from these Animica facts, concisely, with the relevant link:\n${ctx}` }, { role: 'user', content: query }], temperature: 0.1, max_tokens: 512 });
      return text(r.text);
    } catch (e) { return fail(e); }
  });

  server.registerTool('animica_price_quote', {
    title: 'Animica price quote',
    description: 'Estimate the USD cost of a request before running it. Provide token counts OR sample text.',
    inputSchema: {
      model: z.string().optional().describe('Default anm-fast-8b.'),
      input_tokens: z.number().int().nonnegative().optional(),
      output_tokens: z.number().int().nonnegative().optional(),
      input_text: z.string().optional().describe('If given, tokens are estimated from it.'),
      expected_output_text: z.string().optional(),
    },
  }, async ({ model, input_tokens, output_tokens, input_text, expected_output_text }) => {
    const m = model || 'anm-fast-8b';
    const it = input_tokens ?? (input_text ? estimateTokens(input_text) : 1000);
    const ot = output_tokens ?? (expected_output_text ? estimateTokens(expected_output_text) : 500);
    return json({ ...quote(m, it, ot), all_models: pricing() });
  });

  server.registerTool('animica_create_api_key', {
    title: 'Animica create API key',
    description: 'Create a new Animica API key for your account (requires you to be signed in / have an account token). Returns the new key once.',
    inputSchema: { name: z.string().optional().describe('A label for the key.') },
  }, async ({ name }) => {
    try { return json(await gatewayPost('/api-keys', { name: name || 'mcp-generated' })); } catch (e) { return fail(e); }
  });

  server.registerTool('animica_check_usage', {
    title: 'Animica check usage',
    description: 'Report your Animica usage and remaining credits for the current key.',
    inputSchema: {},
  }, async () => {
    try { return json(await gatewayGet('/usage')); } catch (e) { return fail(e); }
  });

  // --- provider / mining (the SUPPLY side) ----------------------------------
  // A bot/operator with spare compute can EARN ANM by powering Animica, not just
  // pay to use it. These tools surface how to join and how it's performing.
  server.registerTool('animica_mining_info', {
    title: 'How to mine / provide Animica',
    description: 'How to earn ANM by powering Animica: connect a miner to the pool (PoIES = hash-share + AI/quantum/storage proofs) and/or serve inference (AICF, earn per request). Returns the stratum endpoint, downloads, and current pool config.',
    inputSchema: {},
  }, async () => {
    try {
      const [cfg, dl] = await Promise.all([
        poolGet(['/api/mining/config']).catch(() => ({})),
        poolGet(['/api/mining/downloads']).catch(() => ({})),
      ]);
      return json({
        how_to_start: [
          `1. Get a wallet address (anim1...) — earnings are paid to it.`,
          `2. Download a miner for your OS from the list below, or point any compatible miner at the stratum endpoint.`,
          `3. Run it with: -o ${STRATUM_URL} -u <your anim1... address>.<worker_name>`,
          `4. You earn ANM via PoIES (proof of useful work) and, if you serve inference, via AICF per-request rewards.`,
        ],
        stratum_endpoint: STRATUM_URL,
        pool_config: cfg,
        downloads: dl,
        docs: 'https://animica.org/mine',
        also_provide_inference: 'Run an AICF/ENA inference worker to earn ANM per served request — see animica_become_provider.',
      });
    } catch (e) { return fail(e); }
  });

  server.registerTool('animica_pool_stats', {
    title: 'Animica pool stats',
    description: 'Live pool health, mode, and miner count — so a bot can evaluate whether mining/providing is worth it.',
    inputSchema: {},
  }, async () => {
    try {
      const [status, miners] = await Promise.all([
        poolGet(['/api/mining/status']).catch(() => ({})),
        poolGet(['/api/miners']).catch(() => ({ items: [] })),
      ]);
      const items = (miners?.items || miners?.data || []);
      return json({ status, miner_count: Array.isArray(items) ? items.length : undefined, sample_workers: Array.isArray(items) ? items.slice(0, 3) : undefined });
    } catch (e) { return fail(e); }
  });

  server.registerTool('animica_mining_status', {
    title: 'Animica miner status',
    description: 'Look up a specific miner/worker (by anim1... address) — hashrate, shares, and earnings.',
    inputSchema: { address: z.string().describe('Your anim1... payout address.') },
  }, async ({ address }) => {
    try {
      const miners = await poolGet(['/api/miners']);
      const items = (miners?.items || miners?.data || []) as any[];
      const mine = items.filter((m) => (m.address === address) || (m.worker_id || '').includes(address));
      return json(mine.length ? mine : { message: `No active worker found for ${address}. Start one with animica_mining_info.`, total_active_workers: items.length });
    } catch (e) { return fail(e); }
  });

  server.registerTool('animica_become_provider', {
    title: 'Become an Animica inference provider',
    description: 'Earn ANM by SERVING inference (the supply side of this MCP). Returns how a GPU/compute owner joins as an AICF/ENA provider and gets paid per request.',
    inputSchema: {},
  }, async () => {
    return json({
      summary: 'Animica pays providers ANM for useful AI work. Run an inference worker; it pulls jobs and earns per served request (AICF) plus pool rewards (PoIES).',
      steps: [
        'Have a GPU (or capable CPU for small models) and an anim1... wallet address.',
        `Connect to the pool: ${STRATUM_URL} (hash-share) and/or run the AICF inference worker to serve LLM jobs.`,
        'Download the worker/miner from animica_mining_info → downloads.',
        'Earnings accrue to your address; track with animica_mining_status.',
      ],
      stratum_endpoint: STRATUM_URL,
      pool: POOL_URL,
      docs: 'https://animica.org/mine',
      note: 'The same network you can consume cheap inference from (animica_chat) also pays you to power it — two-sided.',
    });
  });

  return server;
}

async function main() {
  const transport = (process.env.MCP_TRANSPORT || 'stdio').toLowerCase();
  if (transport === 'http') {
    const app = express();
    app.use(express.json({ limit: '4mb' }));
    app.get('/healthz', (_req, res) => res.json({ ok: true, name: 'animica-mcp', version: VERSION, base: BASE_URL, models_hint: '/models' }));
    // Stateless: a fresh server+transport per request.
    app.post('/mcp', async (req, res) => {
      try {
        const server = buildServer();
        const t = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
        res.on('close', () => { t.close(); server.close(); });
        await server.connect(t);
        await t.handleRequest(req, res, req.body);
      } catch (e) {
        if (!res.headersSent) res.status(500).json({ error: (e as Error).message });
      }
    });
    const port = parseInt(process.env.PORT || '8765', 10);
    app.listen(port, () => console.error(`[animica-mcp] HTTP MCP on :${port}  (wraps ${BASE_URL})`));
  } else {
    const server = buildServer();
    const t = new StdioServerTransport();
    await server.connect(t);
    console.error(`[animica-mcp] stdio ready  (wraps ${BASE_URL})`);
  }
}

main().catch((e) => { console.error('[animica-mcp] fatal:', e); process.exit(1); });
