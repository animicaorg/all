import { createHash, randomBytes } from 'node:crypto';
import { prisma } from './db';
import { seal, open } from './vault';

// Registry of social platforms Animica Animal can be CONNECTED to. Connecting means linking an
// account the OPERATOR ALREADY OWNS via that platform's official OAuth (or pasting a token they
// already hold). There is deliberately NO self-signup / account-creation primitive here — the
// mascot posts to owned channels through sanctioned APIs, nothing else.

export type Flavor = 'oauth2' | 'tiktok' | 'reddit';

export interface PlatformDef {
  key: string;
  label: string;
  emoji: string;
  authorizeUrl: string;
  tokenUrl: string;
  scope: string;
  flavor: Flavor;
  pkce: boolean;
  clientIdEnv: string;
  clientSecretEnv: string;
  media: Array<'text' | 'image' | 'video' | 'music'>;
  supportsMusic: boolean;
  manualOk: boolean; // allow pasting an existing owned-account token when OAuth app isn't set up
  note: string;
}

export const PLATFORMS: Record<string, PlatformDef> = {
  tiktok: {
    key: 'tiktok', label: 'TikTok', emoji: '🎵',
    authorizeUrl: 'https://www.tiktok.com/v2/auth/authorize/',
    tokenUrl: 'https://open.tiktokapis.com/v2/oauth/token/',
    scope: 'user.info.basic,video.publish,video.upload',
    flavor: 'tiktok', pkce: true,
    clientIdEnv: 'TIKTOK_CLIENT_KEY', clientSecretEnv: 'TIKTOK_CLIENT_SECRET',
    media: ['video', 'music'], supportsMusic: true, manualOk: true,
    note: 'Short-form vertical video with a custom generated music/audio track.',
  },
  x: {
    key: 'x', label: 'X (Twitter)', emoji: '𝕏',
    authorizeUrl: 'https://twitter.com/i/oauth2/authorize',
    tokenUrl: 'https://api.twitter.com/2/oauth2/token',
    scope: 'tweet.read tweet.write users.read offline.access media.write',
    flavor: 'oauth2', pkce: true,
    clientIdEnv: 'X_CLIENT_ID', clientSecretEnv: 'X_CLIENT_SECRET',
    media: ['text', 'image', 'video'], supportsMusic: false, manualOk: true,
    note: 'Text posts with optional image/video.',
  },
  youtube: {
    key: 'youtube', label: 'YouTube', emoji: '▶️',
    authorizeUrl: 'https://accounts.google.com/o/oauth2/v2/auth',
    tokenUrl: 'https://oauth2.googleapis.com/token',
    scope: 'https://www.googleapis.com/auth/youtube.upload',
    flavor: 'oauth2', pkce: false,
    clientIdEnv: 'YOUTUBE_CLIENT_ID', clientSecretEnv: 'YOUTUBE_CLIENT_SECRET',
    media: ['video', 'music'], supportsMusic: true, manualOk: true,
    note: 'YouTube Shorts (vertical video) with custom audio.',
  },
  instagram: {
    key: 'instagram', label: 'Instagram', emoji: '📸',
    authorizeUrl: 'https://api.instagram.com/oauth/authorize',
    tokenUrl: 'https://api.instagram.com/oauth/access_token',
    scope: 'instagram_business_basic,instagram_business_content_publish',
    flavor: 'oauth2', pkce: false,
    clientIdEnv: 'INSTAGRAM_CLIENT_ID', clientSecretEnv: 'INSTAGRAM_CLIENT_SECRET',
    media: ['image', 'video'], supportsMusic: false, manualOk: true,
    note: 'Reels + image posts.',
  },
  reddit: {
    key: 'reddit', label: 'Reddit', emoji: '👽',
    authorizeUrl: 'https://www.reddit.com/api/v1/authorize',
    tokenUrl: 'https://www.reddit.com/api/v1/access_token',
    scope: 'identity submit',
    flavor: 'reddit', pkce: false,
    clientIdEnv: 'REDDIT_CLIENT_ID', clientSecretEnv: 'REDDIT_CLIENT_SECRET',
    media: ['text', 'image'], supportsMusic: false, manualOk: true,
    note: 'Self/link posts to subreddits you moderate or that permit them.',
  },
  mastodon: {
    key: 'mastodon', label: 'Mastodon', emoji: '🐘',
    authorizeUrl: '', tokenUrl: '', // instance-relative; set MASTODON_INSTANCE
    scope: 'read write',
    flavor: 'oauth2', pkce: false,
    clientIdEnv: 'MASTODON_CLIENT_ID', clientSecretEnv: 'MASTODON_CLIENT_SECRET',
    media: ['text', 'image', 'video'], supportsMusic: false, manualOk: true,
    note: 'Toots with media. Set MASTODON_INSTANCE (e.g. https://mastodon.social).',
  },
};

export function platform(key: string): PlatformDef | null {
  return PLATFORMS[key] ?? null;
}

function clientId(p: PlatformDef): string { return process.env[p.clientIdEnv] || ''; }
function clientSecret(p: PlatformDef): string { return process.env[p.clientSecretEnv] || ''; }

export function isConfigured(p: PlatformDef): boolean {
  if (p.key === 'mastodon' && !process.env.MASTODON_INSTANCE) return false;
  return Boolean(clientId(p) && clientSecret(p));
}

function instanceBase(p: PlatformDef): string {
  if (p.key === 'mastodon') return (process.env.MASTODON_INSTANCE || '').replace(/\/+$/, '');
  return '';
}

// ── PKCE ───────────────────────────────────────────────────────────────────
export function pkcePair(): { verifier: string; challenge: string } {
  const verifier = randomBytes(48).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  return { verifier, challenge };
}

export function buildAuthorizeUrl(p: PlatformDef, opts: { state: string; challenge?: string; redirectUri: string }): string {
  const base = p.key === 'mastodon' ? `${instanceBase(p)}/oauth/authorize` : p.authorizeUrl;
  const q = new URLSearchParams({
    response_type: 'code',
    redirect_uri: opts.redirectUri,
    scope: p.scope,
    state: opts.state,
  });
  // TikTok uses client_key; everyone else uses client_id.
  if (p.flavor === 'tiktok') q.set('client_key', clientId(p));
  else q.set('client_id', clientId(p));
  if (p.pkce && opts.challenge) {
    q.set('code_challenge', opts.challenge);
    q.set('code_challenge_method', 'S256');
  }
  if (p.key === 'reddit') { q.set('duration', 'permanent'); }
  return `${base}?${q.toString()}`;
}

export interface TokenResult {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  scope: string;
}

export async function exchangeCode(p: PlatformDef, opts: { code: string; verifier?: string; redirectUri: string }): Promise<TokenResult> {
  const url = p.key === 'mastodon' ? `${instanceBase(p)}/oauth/token` : p.tokenUrl;
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code: opts.code,
    redirect_uri: opts.redirectUri,
  });
  const headers: Record<string, string> = { 'content-type': 'application/x-www-form-urlencoded', accept: 'application/json' };
  if (p.flavor === 'tiktok') {
    body.set('client_key', clientId(p));
    body.set('client_secret', clientSecret(p));
  } else if (p.flavor === 'reddit') {
    headers['authorization'] = 'Basic ' + Buffer.from(`${clientId(p)}:${clientSecret(p)}`).toString('base64');
    headers['user-agent'] = 'animica-animal/1.0';
  } else {
    body.set('client_id', clientId(p));
    body.set('client_secret', clientSecret(p));
  }
  if (opts.verifier) body.set('code_verifier', opts.verifier);

  const res = await fetch(url, { method: 'POST', headers, body });
  const j: any = await res.json().catch(() => ({}));
  if (!res.ok || j.error) {
    throw new Error(`token exchange failed (${res.status}): ${j.error_description || j.error || 'unknown'}`);
  }
  return {
    accessToken: j.access_token || '',
    refreshToken: j.refresh_token || '',
    expiresIn: Number(j.expires_in || 0),
    scope: j.scope || p.scope,
  };
}

// ── connection storage ───────────────────────────────────────────────────────
export interface PublicConnection {
  platform: string;
  label: string;
  emoji: string;
  status: string;
  handle: string;
  displayName: string;
  autoPost: boolean;
  configured: boolean;
  manualOk: boolean;
  supportsMusic: boolean;
  note: string;
  connectedAt: string | null;
  lastPostAt: string | null;
}

export async function listConnectionsPublic(): Promise<PublicConnection[]> {
  const rows = await prisma.socialConnection.findMany();
  const byKey = new Map(rows.map((r) => [r.platform, r]));
  return Object.values(PLATFORMS).map((p) => {
    const r = byKey.get(p.key);
    return {
      platform: p.key, label: p.label, emoji: p.emoji,
      status: r?.status ?? 'DISCONNECTED',
      handle: r?.handle ?? '', displayName: r?.displayName ?? '',
      autoPost: r?.autoPost ?? true,
      configured: isConfigured(p), manualOk: p.manualOk, supportsMusic: p.supportsMusic,
      note: p.note,
      connectedAt: r?.connectedAt ? r.connectedAt.toISOString() : null,
      lastPostAt: r?.lastPostAt ? r.lastPostAt.toISOString() : null,
    };
  });
}

export async function saveTokens(platformKey: string, opts: {
  handle?: string; displayName?: string; externalId?: string; scope?: string;
  accessToken: string; refreshToken?: string; expiresIn?: number; meta?: any;
}) {
  const expiresAt = opts.expiresIn ? new Date(Date.now() + opts.expiresIn * 1000) : null;
  const data = {
    status: 'CONNECTED',
    handle: opts.handle ?? '',
    displayName: opts.displayName ?? '',
    externalId: opts.externalId ?? '',
    scope: opts.scope ?? '',
    accessTokenEnc: seal(opts.accessToken),
    refreshTokenEnc: opts.refreshToken ? seal(opts.refreshToken) : null,
    tokenExpiresAt: expiresAt,
    metaJson: JSON.stringify(opts.meta ?? {}),
    connectedAt: new Date(),
  };
  await prisma.socialConnection.upsert({
    where: { platform: platformKey },
    create: { platform: platformKey, ...data },
    update: data,
  });
}

export async function disconnect(platformKey: string) {
  await prisma.socialConnection.upsert({
    where: { platform: platformKey },
    create: { platform: platformKey, status: 'DISCONNECTED' },
    update: { status: 'DISCONNECTED', accessTokenEnc: null, refreshTokenEnc: null, tokenExpiresAt: null, handle: '', displayName: '', connectedAt: null },
  });
}

export async function setAutoPost(platformKey: string, on: boolean) {
  await prisma.socialConnection.update({ where: { platform: platformKey }, data: { autoPost: on } }).catch(() => {});
}

// INTERNAL ONLY — unsealed tokens for the localhost posting engine. Never call from a browser path.
export async function connectionsForEngine(): Promise<Array<{
  platform: string; handle: string; status: string; autoPost: boolean; supportsMusic: boolean;
  accessToken: string; refreshToken: string; scope: string; expiresAt: string | null; media: string[];
}>> {
  const rows = await prisma.socialConnection.findMany({ where: { status: 'CONNECTED' } });
  return rows.map((r) => {
    const p = PLATFORMS[r.platform];
    return {
      platform: r.platform, handle: r.handle, status: r.status, autoPost: r.autoPost,
      supportsMusic: p?.supportsMusic ?? false,
      accessToken: open(r.accessTokenEnc), refreshToken: open(r.refreshTokenEnc),
      scope: r.scope, expiresAt: r.tokenExpiresAt ? r.tokenExpiresAt.toISOString() : null,
      media: p?.media ?? ['text'],
    };
  });
}
