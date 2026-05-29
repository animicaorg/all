// Agent tool registry.
//
// A tool exposes:
//   - an OpenAI-style JSON schema the model uses to construct calls
//   - a zod schema for runtime argument validation (the model loves to
//     ship bad JSON; never trust it)
//   - an `execute` function that runs server-side with the current
//     user's context
//   - a write-policy that controls whether the agent can auto-execute
//     or must request user approval first
//
// Tools live next to the assistant modes that allow them; the runtime
// only exposes a tool to a turn when both the mode and the user's
// linked integrations make it usable.

import { z, ZodTypeAny } from 'zod';
import { PrismaClient, User } from '@prisma/client';
import { env } from '../env';
import * as github from './integrations/github';
import * as gitlab from './integrations/gitlab';

export type WritePolicy = 'read' | 'ask' | 'allow' | 'deny';

export interface ToolContext {
  prisma: PrismaClient;
  user: User;
  // Per-call escape hatch the user can flip via the UI confirm prompt.
  approved?: boolean;
}

export interface ToolResult {
  ok: boolean;
  // Either a structured payload or an error message; whatever is
  // returned here gets serialized into the assistant's next turn.
  data?: unknown;
  error?: string;
  // Optional artifact URL (PR, commit, etc.) surfaced in the UI.
  artifactUrl?: string;
}

export interface Tool {
  name: string;
  description: string;
  // OpenAI tool format (compatible with any provider speaking the
  // /v1/chat/completions tool schema).
  jsonSchema: Record<string, unknown>;
  // Runtime validator. The model often hallucinates keys; zod refuses.
  schema: ZodTypeAny;
  writePolicy: WritePolicy;
  // Whether the user must have a linked integration to use this tool.
  requires?: 'github' | 'gitlab';
  execute(args: unknown, ctx: ToolContext): Promise<ToolResult>;
}

// --------------------------- GitHub tools -------------------------------- //

const ghListReposSchema = z.object({
  visibility: z.enum(['public', 'private', 'all']).optional(),
  perPage: z.number().int().min(1).max(100).optional(),
});

const ghGetRepoSchema = z.object({
  owner: z.string(),
  repo: z.string(),
});

const ghReadFileSchema = z.object({
  owner: z.string(),
  repo: z.string(),
  path: z.string(),
  ref: z.string().optional(),
});

const ghProposeChangeSchema = z.object({
  owner: z.string(),
  repo: z.string(),
  baseBranch: z.string().default('main'),
  headBranch: z.string(),
  title: z.string(),
  body: z.string().optional(),
  files: z
    .array(
      z.object({
        path: z.string(),
        content: z.string(),
        message: z.string().optional(),
      }),
    )
    .min(1),
});

const githubTools: Tool[] = [
  {
    name: 'github_list_repos',
    description: 'List repositories the linked GitHub user can access.',
    writePolicy: 'read',
    requires: 'github',
    schema: ghListReposSchema,
    jsonSchema: {
      type: 'object',
      properties: {
        visibility: { type: 'string', enum: ['public', 'private', 'all'] },
        perPage: { type: 'integer', minimum: 1, maximum: 100 },
      },
    },
    async execute(args, ctx) {
      const a = ghListReposSchema.parse(args);
      return github.listRepos(ctx, a);
    },
  },
  {
    name: 'github_get_repo',
    description: 'Fetch metadata about a GitHub repository.',
    writePolicy: 'read',
    requires: 'github',
    schema: ghGetRepoSchema,
    jsonSchema: {
      type: 'object',
      required: ['owner', 'repo'],
      properties: { owner: { type: 'string' }, repo: { type: 'string' } },
    },
    async execute(args, ctx) {
      const a = ghGetRepoSchema.parse(args);
      return github.getRepo(ctx, a);
    },
  },
  {
    name: 'github_read_file',
    description: 'Read a file from a GitHub repository at a given ref.',
    writePolicy: 'read',
    requires: 'github',
    schema: ghReadFileSchema,
    jsonSchema: {
      type: 'object',
      required: ['owner', 'repo', 'path'],
      properties: {
        owner: { type: 'string' },
        repo: { type: 'string' },
        path: { type: 'string' },
        ref: { type: 'string' },
      },
    },
    async execute(args, ctx) {
      const a = ghReadFileSchema.parse(args);
      return github.readFile(ctx, a);
    },
  },
  {
    name: 'github_propose_change',
    description:
      'Open a draft pull request on the user\'s behalf. Creates the head branch from base if missing, writes each provided file, and opens the PR for review. Never commits to the base branch directly.',
    // "ask" — surface a confirm step in the UI before running.
    writePolicy: env.AGENT_DEFAULT_WRITE_POLICY,
    requires: 'github',
    schema: ghProposeChangeSchema,
    jsonSchema: {
      type: 'object',
      required: ['owner', 'repo', 'headBranch', 'title', 'files'],
      properties: {
        owner: { type: 'string' },
        repo: { type: 'string' },
        baseBranch: { type: 'string', default: 'main' },
        headBranch: { type: 'string' },
        title: { type: 'string' },
        body: { type: 'string' },
        files: {
          type: 'array',
          minItems: 1,
          items: {
            type: 'object',
            required: ['path', 'content'],
            properties: {
              path: { type: 'string' },
              content: { type: 'string' },
              message: { type: 'string' },
            },
          },
        },
      },
    },
    async execute(args, ctx) {
      const a = ghProposeChangeSchema.parse(args);
      return github.proposeChange(ctx, a);
    },
  },
];

// --------------------------- GitLab tools -------------------------------- //

const glListProjectsSchema = z.object({
  perPage: z.number().int().min(1).max(100).optional(),
  membership: z.boolean().optional(),
});

const glReadFileSchema = z.object({
  projectId: z.union([z.number().int(), z.string()]),
  filePath: z.string(),
  ref: z.string().optional(),
});

const glProposeChangeSchema = z.object({
  projectId: z.union([z.number().int(), z.string()]),
  baseBranch: z.string().default('main'),
  headBranch: z.string(),
  title: z.string(),
  description: z.string().optional(),
  files: z
    .array(
      z.object({ path: z.string(), content: z.string(), action: z.enum(['create', 'update']).default('update') }),
    )
    .min(1),
});

const gitlabTools: Tool[] = [
  {
    name: 'gitlab_list_projects',
    description: 'List GitLab projects accessible to the linked user.',
    writePolicy: 'read',
    requires: 'gitlab',
    schema: glListProjectsSchema,
    jsonSchema: {
      type: 'object',
      properties: {
        perPage: { type: 'integer', minimum: 1, maximum: 100 },
        membership: { type: 'boolean' },
      },
    },
    async execute(args, ctx) {
      const a = glListProjectsSchema.parse(args);
      return gitlab.listProjects(ctx, a);
    },
  },
  {
    name: 'gitlab_read_file',
    description: 'Read a file from a GitLab project at a given ref.',
    writePolicy: 'read',
    requires: 'gitlab',
    schema: glReadFileSchema,
    jsonSchema: {
      type: 'object',
      required: ['projectId', 'filePath'],
      properties: {
        projectId: { type: ['string', 'integer'] },
        filePath: { type: 'string' },
        ref: { type: 'string' },
      },
    },
    async execute(args, ctx) {
      const a = glReadFileSchema.parse(args);
      return gitlab.readFile(ctx, a);
    },
  },
  {
    name: 'gitlab_propose_change',
    description:
      'Open a draft merge request: create the head branch from base, commit each file, open the MR. Never commits to the base branch directly.',
    writePolicy: env.AGENT_DEFAULT_WRITE_POLICY,
    requires: 'gitlab',
    schema: glProposeChangeSchema,
    jsonSchema: {
      type: 'object',
      required: ['projectId', 'headBranch', 'title', 'files'],
      properties: {
        projectId: { type: ['string', 'integer'] },
        baseBranch: { type: 'string', default: 'main' },
        headBranch: { type: 'string' },
        title: { type: 'string' },
        description: { type: 'string' },
        files: {
          type: 'array',
          minItems: 1,
          items: {
            type: 'object',
            required: ['path', 'content'],
            properties: {
              path: { type: 'string' },
              content: { type: 'string' },
              action: { type: 'string', enum: ['create', 'update'] },
            },
          },
        },
      },
    },
    async execute(args, ctx) {
      const a = glProposeChangeSchema.parse(args);
      return gitlab.proposeChange(ctx, a);
    },
  },
];

// --------------------------- Web tools ---------------------------------- //

const webFetchSchema = z.object({
  url: z.string().url(),
  maxBytes: z.number().int().min(1).max(200_000).optional(),
});

const webTools: Tool[] = [
  {
    name: 'web_fetch',
    description:
      'Fetch the body of an HTTP(S) URL as text. Use it to look at documentation, blog posts, ' +
      'or other public web pages the agent needs as context. Binary responses are truncated.',
    writePolicy: 'read',
    schema: webFetchSchema,
    jsonSchema: {
      type: 'object',
      required: ['url'],
      properties: {
        url: { type: 'string', description: 'Absolute http(s) URL.' },
        maxBytes: { type: 'integer', description: 'Cap on body bytes (default 64KB).' },
      },
    },
    async execute(args) {
      const a = webFetchSchema.parse(args);
      try {
        const res = await fetch(a.url, {
          // Limit total request time so a slow site can't stall a turn.
          signal: AbortSignal.timeout(15_000),
          // Be a courteous bot.
          headers: { 'user-agent': 'Animica-Chat-Agent/0.1 (+https://animica.org)' },
        });
        const ct = res.headers.get('content-type') || '';
        const cap = a.maxBytes ?? 65536;
        const ab = await res.arrayBuffer();
        const truncated = ab.byteLength > cap;
        const slice = Buffer.from(ab).subarray(0, cap).toString('utf8');
        return {
          ok: res.ok,
          data: {
            url: res.url,
            status: res.status,
            contentType: ct,
            truncated,
            byteLength: ab.byteLength,
            body: slice,
          },
        };
      } catch (err) {
        return { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
    },
  },
];

// ---------------------- Animica chain tools ----------------------------- //

const animicaRpcSchema = z.object({
  method: z.string().min(1),
  params: z.any().optional(),
});

const animicaWalletBalanceSchema = z.object({
  address: z.string().min(8),
});

const animicaTools: Tool[] = [
  {
    name: 'animica_rpc',
    description:
      'Call an arbitrary Animica node RPC method (e.g. "state.getBalance", "chain.getHead", ' +
      '"miner.getBlockTemplate"). Returns the raw result. Use it for read-only chain queries; ' +
      'no transaction signing happens here.',
    writePolicy: 'read',
    schema: animicaRpcSchema,
    jsonSchema: {
      type: 'object',
      required: ['method'],
      properties: {
        method: { type: 'string', description: 'RPC method name (e.g. state.getBalance).' },
        params: { description: 'Optional params object or array passed to the method.' },
      },
    },
    async execute(args) {
      const a = animicaRpcSchema.parse(args);
      try {
        const rpcUrl = process.env.ANIMICA_RPC_URL || 'http://127.0.0.1:8545/rpc';
        const res = await fetch(rpcUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            jsonrpc: '2.0',
            id: 1,
            method: a.method,
            params: a.params ?? {},
          }),
          signal: AbortSignal.timeout(10_000),
        });
        const body = (await res.json()) as { result?: unknown; error?: unknown };
        if (body.error) return { ok: false, error: JSON.stringify(body.error) };
        return { ok: true, data: body.result };
      } catch (err) {
        return { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
    },
  },
  {
    name: 'animica_wallet_balance',
    description: 'Get the ANM balance of an Animica wallet address.',
    writePolicy: 'read',
    schema: animicaWalletBalanceSchema,
    jsonSchema: {
      type: 'object',
      required: ['address'],
      properties: { address: { type: 'string', description: 'anim1… wallet address.' } },
    },
    async execute(args) {
      const a = animicaWalletBalanceSchema.parse(args);
      try {
        const rpcUrl = process.env.ANIMICA_RPC_URL || 'http://127.0.0.1:8545/rpc';
        const res = await fetch(rpcUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            jsonrpc: '2.0',
            id: 1,
            method: 'state.getBalance',
            params: { address: a.address },
          }),
          signal: AbortSignal.timeout(10_000),
        });
        const body = (await res.json()) as { result?: unknown; error?: unknown };
        if (body.error) return { ok: false, error: JSON.stringify(body.error) };
        return { ok: true, data: body.result };
      } catch (err) {
        return { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
    },
  },
];

// --------------------------- Registry ----------------------------------- //

const ALL_TOOLS: Tool[] = [
  ...githubTools,
  ...gitlabTools,
  ...webTools,
  ...animicaTools,
];

export function listTools(): Tool[] {
  return ALL_TOOLS;
}

export function findTool(name: string): Tool | undefined {
  return ALL_TOOLS.find((t) => t.name === name);
}

// Filter the catalog to what the model can see for a specific turn:
// the assistant mode must allow the tool AND the user must have the
// required integration linked.
export async function availableToolsForUser(opts: {
  user: User;
  prisma: PrismaClient;
  modeAllowed: string[];
}): Promise<Tool[]> {
  const allowed = new Set(opts.modeAllowed);
  const filtered = ALL_TOOLS.filter((t) => allowed.has(t.name));
  if (filtered.length === 0) return [];
  const ghLink = await opts.prisma.gitHubLink.findUnique({ where: { userId: opts.user.id } });
  const glLink = await opts.prisma.gitLabLink.findUnique({ where: { userId: opts.user.id } });
  return filtered.filter((t) => {
    if (t.requires === 'github' && !ghLink) return false;
    if (t.requires === 'gitlab' && !glLink) return false;
    return true;
  });
}

// OpenAI request shape — `tools: [{ type: "function", function: {...} }]`.
export function asOpenAITools(tools: Tool[]) {
  return tools.map((t) => ({
    type: 'function' as const,
    function: {
      name: t.name,
      description: t.description,
      parameters: t.jsonSchema,
    },
  }));
}
