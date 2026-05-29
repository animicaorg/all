// GitLab integration: OAuth + @gitbeaker/rest-backed tool execution.
// Mirrors the GitHub shape so the agent layer can call them
// symmetrically.

import { Gitlab } from '@gitbeaker/rest';
import { env } from '../../env';
import { decrypt, encrypt } from '../../lib/secretBox';
import type { ToolContext, ToolResult } from '../toolRegistry';

export interface GitLabOAuthExchange {
  accessToken: string;
  refreshToken?: string;
  expiresAt?: Date;
  scopes: string[];
  host: string;
}

function host(): string {
  return env.GITLAB_HOST.replace(/\/+$/, '');
}

export function authorizeUrl(state: string): string {
  const params = new URLSearchParams({
    client_id: env.GITLAB_OAUTH_CLIENT_ID,
    redirect_uri: `${env.PUBLIC_BASE_URL}/api/integrations/gitlab/callback`,
    response_type: 'code',
    state,
    scope: env.GITLAB_OAUTH_SCOPES.split(',').join(' '),
  });
  return `${host()}/oauth/authorize?${params.toString()}`;
}

export async function exchangeCode(code: string): Promise<GitLabOAuthExchange> {
  const res = await fetch(`${host()}/oauth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      grant_type: 'authorization_code',
      client_id: env.GITLAB_OAUTH_CLIENT_ID,
      client_secret: env.GITLAB_OAUTH_CLIENT_SECRET,
      code,
      redirect_uri: `${env.PUBLIC_BASE_URL}/api/integrations/gitlab/callback`,
    }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`gitlab exchange failed (${res.status}): ${body}`);
  }
  const j = (await res.json()) as {
    access_token?: string;
    refresh_token?: string;
    expires_in?: number;
    scope?: string;
  };
  if (!j.access_token) throw new Error('gitlab exchange missing access_token');
  return {
    accessToken: j.access_token,
    refreshToken: j.refresh_token,
    expiresAt: j.expires_in ? new Date(Date.now() + j.expires_in * 1000) : undefined,
    scopes: (j.scope || '').split(' ').map((s) => s.trim()).filter(Boolean),
    host: host(),
  };
}

export async function whoAmI(accessToken: string): Promise<{ username: string; id: number }> {
  const res = await fetch(`${host()}/api/v4/user`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error(`gitlab whoami failed: ${res.status}`);
  const j = (await res.json()) as { username: string; id: number };
  return { username: j.username, id: j.id };
}

export function encryptToken(token: string): string {
  return encrypt(token);
}

// --------------------------- Tool execution ----------------------------- //

async function clientForUser(ctx: ToolContext) {
  const link = await ctx.prisma.gitLabLink.findUnique({ where: { userId: ctx.user.id } });
  if (!link) throw new Error('GitLab is not linked for this user');
  const accessToken = decrypt(link.accessTokenCipher);
  return new Gitlab({ host: link.gitlabHost || host(), oauthToken: accessToken });
}

export async function listProjects(
  ctx: ToolContext,
  args: { perPage?: number; membership?: boolean },
): Promise<ToolResult> {
  try {
    const gl = await clientForUser(ctx);
    const projects = await gl.Projects.all({
      perPage: args.perPage ?? 30,
      membership: args.membership ?? true,
      orderBy: 'last_activity_at',
      sort: 'desc',
    });
    return {
      ok: true,
      data: (projects as Array<{ id: number; path_with_namespace: string; default_branch: string; description: string | null; web_url: string; last_activity_at: string; }>).slice(0, 50).map((p) => ({
        id: p.id,
        pathWithNamespace: p.path_with_namespace,
        defaultBranch: p.default_branch,
        description: p.description,
        url: p.web_url,
        lastActivityAt: p.last_activity_at,
      })),
    };
  } catch (err) {
    return { ok: false, error: errorMessage(err) };
  }
}

export async function readFile(
  ctx: ToolContext,
  args: { projectId: number | string; filePath: string; ref?: string },
): Promise<ToolResult> {
  try {
    const gl = await clientForUser(ctx);
    const file = await gl.RepositoryFiles.show(args.projectId, args.filePath, args.ref || 'HEAD');
    const content = Buffer.from((file as { content: string; encoding: string }).content, (file as { encoding: BufferEncoding }).encoding).toString('utf8');
    return { ok: true, data: { path: (file as { file_path: string }).file_path, content, sha: (file as { blob_id: string }).blob_id } };
  } catch (err) {
    return { ok: false, error: errorMessage(err) };
  }
}

export async function proposeChange(
  ctx: ToolContext,
  args: {
    projectId: number | string;
    baseBranch: string;
    headBranch: string;
    title: string;
    description?: string;
    files: { path: string; content: string; action: 'create' | 'update' }[];
  },
): Promise<ToolResult> {
  if (!ctx.approved) {
    return { ok: false, error: 'tool requires user approval' };
  }
  try {
    const gl = await clientForUser(ctx);
    // 1. Branch from base.
    try {
      await gl.Branches.create(args.projectId, args.headBranch, args.baseBranch);
    } catch (err) {
      if (!/already exists/i.test(errorMessage(err))) throw err;
    }
    // 2. Commit all files in one shot for atomicity.
    const actions = args.files.map((f) => ({
      action: f.action,
      filePath: f.path,
      content: f.content,
    }));
    await gl.Commits.create(args.projectId, args.headBranch, `chore: animica chat agent — ${args.title}`, actions);
    // 3. Open the MR as draft.
    const mr = await gl.MergeRequests.create(
      args.projectId,
      args.headBranch,
      args.baseBranch,
      `Draft: ${args.title}`,
      {
        description:
          args.description ||
          'Opened by the Animica Chat coding agent. Please review before merging.',
      },
    );
    return {
      ok: true,
      data: { url: (mr as { web_url: string }).web_url, iid: (mr as { iid: number }).iid },
      artifactUrl: (mr as { web_url: string }).web_url,
    };
  } catch (err) {
    return { ok: false, error: errorMessage(err) };
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
