// GitHub integration: OAuth exchange + Octokit-backed tool execution.
//
// Tokens are decrypted from GitHubLink on every call rather than cached
// in process memory; that keeps a leaked heap dump from leaking
// long-lived credentials.

import { Octokit } from '@octokit/rest';
import { env } from '../../env';
import { decrypt, encrypt } from '../../lib/secretBox';
import type { ToolContext, ToolResult } from '../toolRegistry';

// ---------------------------- OAuth ------------------------------------- //

export interface GitHubOAuthExchange {
  accessToken: string;
  refreshToken?: string;
  expiresAt?: Date;
  scopes: string[];
}

export function authorizeUrl(state: string): string {
  const params = new URLSearchParams({
    client_id: env.GITHUB_OAUTH_CLIENT_ID,
    scope: env.GITHUB_OAUTH_SCOPES.split(',').join(' '),
    redirect_uri: `${env.PUBLIC_BASE_URL}/api/integrations/github/callback`,
    state,
    allow_signup: 'true',
  });
  return `https://github.com/login/oauth/authorize?${params.toString()}`;
}

export async function exchangeCode(code: string): Promise<GitHubOAuthExchange> {
  const res = await fetch('https://github.com/login/oauth/access_token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      client_id: env.GITHUB_OAUTH_CLIENT_ID,
      client_secret: env.GITHUB_OAUTH_CLIENT_SECRET,
      code,
      redirect_uri: `${env.PUBLIC_BASE_URL}/api/integrations/github/callback`,
    }),
  });
  if (!res.ok) throw new Error(`github exchange failed: ${res.status}`);
  const j = (await res.json()) as {
    access_token?: string;
    refresh_token?: string;
    expires_in?: number;
    scope?: string;
    error?: string;
    error_description?: string;
  };
  if (!j.access_token) {
    throw new Error(j.error_description || j.error || 'github exchange missing access_token');
  }
  const scopes = (j.scope || '').split(',').map((s) => s.trim()).filter(Boolean);
  return {
    accessToken: j.access_token,
    refreshToken: j.refresh_token,
    expiresAt: j.expires_in ? new Date(Date.now() + j.expires_in * 1000) : undefined,
    scopes,
  };
}

export async function whoAmI(accessToken: string): Promise<{ login: string; id: number }> {
  const o = new Octokit({ auth: accessToken });
  const { data } = await o.users.getAuthenticated();
  return { login: data.login, id: data.id };
}

// Lightweight repo lister callable from HTTP routes (no ToolContext
// wrapping). Mirrors the shape returned by the tool-call variant so
// the frontend has a single schema to deal with.
export async function listReposWithToken(
  accessToken: string,
  opts: { perPage?: number; visibility?: 'public' | 'private' | 'all' } = {},
): Promise<
  Array<{
    fullName: string;
    private: boolean;
    defaultBranch: string;
    description: string | null;
    url: string;
    pushedAt: string | null;
  }>
> {
  const o = new Octokit({ auth: accessToken });
  const { data } = await o.repos.listForAuthenticatedUser({
    per_page: opts.perPage ?? 50,
    visibility: opts.visibility ?? 'all',
    sort: 'pushed',
  });
  return data.map((r) => ({
    fullName: r.full_name,
    private: r.private,
    defaultBranch: r.default_branch,
    description: r.description,
    url: r.html_url,
    pushedAt: r.pushed_at,
  }));
}

export function encryptToken(token: string): string {
  return encrypt(token);
}

// --------------------------- Tool execution ----------------------------- //

async function octokitForUser(ctx: ToolContext): Promise<Octokit> {
  const link = await ctx.prisma.gitHubLink.findUnique({ where: { userId: ctx.user.id } });
  if (!link) throw new Error('GitHub is not linked for this user');
  const accessToken = decrypt(link.accessTokenCipher);
  return new Octokit({ auth: accessToken });
}

export async function listRepos(
  ctx: ToolContext,
  args: { visibility?: 'public' | 'private' | 'all'; perPage?: number },
): Promise<ToolResult> {
  try {
    const o = await octokitForUser(ctx);
    const { data } = await o.repos.listForAuthenticatedUser({
      per_page: args.perPage ?? 30,
      visibility: args.visibility ?? 'all',
      sort: 'pushed',
    });
    return {
      ok: true,
      data: data.map((r) => ({
        fullName: r.full_name,
        private: r.private,
        defaultBranch: r.default_branch,
        description: r.description,
        url: r.html_url,
        pushedAt: r.pushed_at,
      })),
    };
  } catch (err) {
    return { ok: false, error: errorMessage(err) };
  }
}

export async function getRepo(
  ctx: ToolContext,
  args: { owner: string; repo: string },
): Promise<ToolResult> {
  try {
    const o = await octokitForUser(ctx);
    const { data } = await o.repos.get({ owner: args.owner, repo: args.repo });
    return {
      ok: true,
      data: {
        fullName: data.full_name,
        defaultBranch: data.default_branch,
        description: data.description,
        topics: data.topics,
        url: data.html_url,
        private: data.private,
      },
    };
  } catch (err) {
    return { ok: false, error: errorMessage(err) };
  }
}

export async function readFile(
  ctx: ToolContext,
  args: { owner: string; repo: string; path: string; ref?: string },
): Promise<ToolResult> {
  try {
    const o = await octokitForUser(ctx);
    const { data } = await o.repos.getContent({
      owner: args.owner,
      repo: args.repo,
      path: args.path,
      ref: args.ref,
    });
    if (Array.isArray(data)) {
      return { ok: false, error: 'path is a directory' };
    }
    if (data.type !== 'file' || !('content' in data)) {
      return { ok: false, error: `unsupported content type: ${data.type}` };
    }
    const content = Buffer.from(data.content, data.encoding as BufferEncoding).toString('utf8');
    return { ok: true, data: { path: data.path, sha: data.sha, content } };
  } catch (err) {
    return { ok: false, error: errorMessage(err) };
  }
}

export async function proposeChange(
  ctx: ToolContext,
  args: {
    owner: string;
    repo: string;
    baseBranch: string;
    headBranch: string;
    title: string;
    body?: string;
    files: { path: string; content: string; message?: string }[];
  },
): Promise<ToolResult> {
  // Write tool — only run when explicitly approved by the user.
  if (!ctx.approved) {
    return {
      ok: false,
      error: 'tool requires user approval',
    };
  }
  try {
    const o = await octokitForUser(ctx);
    // 1. Resolve base ref SHA.
    const baseRef = await o.git.getRef({
      owner: args.owner,
      repo: args.repo,
      ref: `heads/${args.baseBranch}`,
    });
    const baseSha = baseRef.data.object.sha;

    // 2. Create the head branch if it doesn't already exist.
    try {
      await o.git.createRef({
        owner: args.owner,
        repo: args.repo,
        ref: `refs/heads/${args.headBranch}`,
        sha: baseSha,
      });
    } catch (err) {
      // 422 = ref already exists; reuse it. Anything else bubbles up.
      const msg = errorMessage(err);
      if (!/already exists/i.test(msg)) throw err;
    }

    // 3. PUT each file via the contents API on the head branch.
    for (const f of args.files) {
      // PUT contents needs the current sha when the file already
      // exists; absent for new files.
      let sha: string | undefined;
      try {
        const cur = await o.repos.getContent({
          owner: args.owner,
          repo: args.repo,
          path: f.path,
          ref: args.headBranch,
        });
        if (!Array.isArray(cur.data) && 'sha' in cur.data) {
          sha = cur.data.sha;
        }
      } catch {
        // 404 — file doesn't exist yet; sha stays undefined.
      }
      await o.repos.createOrUpdateFileContents({
        owner: args.owner,
        repo: args.repo,
        path: f.path,
        message: f.message || `chore: animica chat agent — update ${f.path}`,
        content: Buffer.from(f.content, 'utf8').toString('base64'),
        branch: args.headBranch,
        sha,
      });
    }

    // 4. Open the PR as a draft so a human reviews.
    const pr = await o.pulls.create({
      owner: args.owner,
      repo: args.repo,
      base: args.baseBranch,
      head: args.headBranch,
      title: args.title,
      body:
        args.body ||
        'Opened by the Animica Chat coding agent. Please review the diff before merging.',
      draft: true,
    });

    return {
      ok: true,
      data: { url: pr.data.html_url, number: pr.data.number },
      artifactUrl: pr.data.html_url,
    };
  } catch (err) {
    return { ok: false, error: errorMessage(err) };
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
