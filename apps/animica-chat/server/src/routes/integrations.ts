// OAuth link/unlink for GitHub + GitLab. The state token is a HMAC of
// the user's session id, signed with JWT_SECRET, so the callback can
// recover the user without trusting query-string state alone.

import { Router } from 'express';
import { createHmac } from 'crypto';
import { prisma } from '../prisma';
import { requireAuth } from '../middleware/auth';
import * as github from '../services/integrations/github';
import * as gitlab from '../services/integrations/gitlab';
import { decrypt } from '../lib/secretBox';
import { env } from '../env';

export const integrationsRouter = Router();

function signState(userId: string, provider: 'github' | 'gitlab'): string {
  const payload = `${provider}.${userId}.${Date.now()}`;
  const sig = createHmac('sha256', env.JWT_SECRET).update(payload).digest('base64url');
  return `${payload}.${sig}`;
}

function verifyState(state: string, provider: 'github' | 'gitlab'): string | null {
  const parts = state.split('.');
  if (parts.length !== 4) return null;
  const [stateProvider, userId, ts, sig] = parts as [string, string, string, string];
  if (stateProvider !== provider) return null;
  // Reject states older than 15 min.
  if (Date.now() - Number(ts) > 15 * 60_000) return null;
  const expected = createHmac('sha256', env.JWT_SECRET)
    .update(`${provider}.${userId}.${ts}`)
    .digest('base64url');
  if (expected !== sig) return null;
  return userId;
}

// ----- GitHub ----------------------------------------------------------- //

integrationsRouter.get('/github/start', requireAuth, (req, res) => {
  // Fail loudly if the server is missing OAuth credentials, so the user
  // sees an actionable message rather than bouncing into GitHub's 404
  // page for an empty client_id.
  if (!process.env.GITHUB_OAUTH_CLIENT_ID || !process.env.GITHUB_OAUTH_CLIENT_SECRET) {
    res.status(503).send(
      'GitHub OAuth is not configured on this server yet. ' +
        'Operator: register an OAuth app at github.com/settings/developers ' +
        'with callback https://animica.org/api/integrations/github/callback ' +
        'and set GITHUB_OAUTH_CLIENT_ID / GITHUB_OAUTH_CLIENT_SECRET in chat.env.',
    );
    return;
  }
  const state = signState(req.user!.id, 'github');
  res.redirect(github.authorizeUrl(state));
});

integrationsRouter.get('/github/callback', async (req, res) => {
  const code = String(req.query.code || '');
  const state = String(req.query.state || '');
  const userId = verifyState(state, 'github');
  if (!code || !userId) {
    res.status(400).send('invalid_state');
    return;
  }
  try {
    const tok = await github.exchangeCode(code);
    const who = await github.whoAmI(tok.accessToken);
    await prisma.gitHubLink.upsert({
      where: { userId },
      create: {
        userId,
        githubLogin: who.login,
        githubUserId: who.id,
        accessTokenCipher: github.encryptToken(tok.accessToken),
        refreshTokenCipher: tok.refreshToken ? github.encryptToken(tok.refreshToken) : null,
        tokenExpiresAt: tok.expiresAt,
        scopes: tok.scopes,
      },
      update: {
        githubLogin: who.login,
        githubUserId: who.id,
        accessTokenCipher: github.encryptToken(tok.accessToken),
        refreshTokenCipher: tok.refreshToken ? github.encryptToken(tok.refreshToken) : null,
        tokenExpiresAt: tok.expiresAt,
        scopes: tok.scopes,
      },
    });
    res.redirect('/tools?linked=github');
  } catch (err) {
    res.status(500).send(`github_link_failed: ${err instanceof Error ? err.message : err}`);
  }
});

integrationsRouter.post('/github/disconnect', requireAuth, async (req, res) => {
  await prisma.gitHubLink.deleteMany({ where: { userId: req.user!.id } });
  res.json({ ok: true });
});

// ----- GitLab ----------------------------------------------------------- //

integrationsRouter.get('/gitlab/start', requireAuth, (req, res) => {
  const state = signState(req.user!.id, 'gitlab');
  res.redirect(gitlab.authorizeUrl(state));
});

integrationsRouter.get('/gitlab/callback', async (req, res) => {
  const code = String(req.query.code || '');
  const state = String(req.query.state || '');
  const userId = verifyState(state, 'gitlab');
  if (!code || !userId) {
    res.status(400).send('invalid_state');
    return;
  }
  try {
    const tok = await gitlab.exchangeCode(code);
    const who = await gitlab.whoAmI(tok.accessToken);
    await prisma.gitLabLink.upsert({
      where: { userId },
      create: {
        userId,
        gitlabHost: tok.host,
        gitlabUsername: who.username,
        gitlabUserId: who.id,
        accessTokenCipher: gitlab.encryptToken(tok.accessToken),
        refreshTokenCipher: tok.refreshToken ? gitlab.encryptToken(tok.refreshToken) : null,
        tokenExpiresAt: tok.expiresAt,
        scopes: tok.scopes,
      },
      update: {
        gitlabHost: tok.host,
        gitlabUsername: who.username,
        gitlabUserId: who.id,
        accessTokenCipher: gitlab.encryptToken(tok.accessToken),
        refreshTokenCipher: tok.refreshToken ? gitlab.encryptToken(tok.refreshToken) : null,
        tokenExpiresAt: tok.expiresAt,
        scopes: tok.scopes,
      },
    });
    res.redirect('/tools?linked=gitlab');
  } catch (err) {
    res.status(500).send(`gitlab_link_failed: ${err instanceof Error ? err.message : err}`);
  }
});

integrationsRouter.post('/gitlab/disconnect', requireAuth, async (req, res) => {
  await prisma.gitLabLink.deleteMany({ where: { userId: req.user!.id } });
  res.json({ ok: true });
});

integrationsRouter.get('/status', requireAuth, async (req, res) => {
  const [github, gitlab] = await Promise.all([
    prisma.gitHubLink.findUnique({ where: { userId: req.user!.id } }),
    prisma.gitLabLink.findUnique({ where: { userId: req.user!.id } }),
  ]);
  res.json({
    github: github
      ? {
          login: github.githubLogin,
          scopes: github.scopes,
          selectedRepo: github.selectedRepoFullName,
        }
      : null,
    gitlab: gitlab
      ? { username: gitlab.gitlabUsername, host: gitlab.gitlabHost, scopes: gitlab.scopes }
      : null,
  });
});

// List the user's GitHub repos so the UI can offer a picker.
integrationsRouter.get('/github/repos', requireAuth, async (req, res) => {
  const link = await prisma.gitHubLink.findUnique({ where: { userId: req.user!.id } });
  if (!link) {
    res.status(400).json({ error: 'github_not_linked' });
    return;
  }
  try {
    const accessToken = decrypt(link.accessTokenCipher);
    const repos = await github.listReposWithToken(accessToken, {
      visibility: (req.query.visibility as 'public' | 'private' | 'all') || 'all',
      perPage: Math.min(100, Number(req.query.perPage) || 50),
    });
    res.json({ repos, selected: link.selectedRepoFullName });
  } catch (err) {
    res.status(502).json({
      error: 'github_repos_failed',
      detail: err instanceof Error ? err.message : String(err),
    });
  }
});

// Persist (or clear) the user's default repo. Body: { fullName: string | null }.
integrationsRouter.post('/github/repo', requireAuth, async (req, res) => {
  const link = await prisma.gitHubLink.findUnique({ where: { userId: req.user!.id } });
  if (!link) {
    res.status(400).json({ error: 'github_not_linked' });
    return;
  }
  const raw = (req.body && (req.body as { fullName?: unknown }).fullName) ?? null;
  let fullName: string | null = null;
  if (raw !== null) {
    if (typeof raw !== 'string' || !/^[\w.-]+\/[\w.-]+$/.test(raw)) {
      res.status(400).json({ error: 'invalid_repo_full_name' });
      return;
    }
    fullName = raw;
  }
  await prisma.gitHubLink.update({
    where: { userId: req.user!.id },
    data: { selectedRepoFullName: fullName },
  });
  res.json({ ok: true, selected: fullName });
});
