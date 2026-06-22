/**
 * Minimal persistence for the Studio host broker — no native deps.
 *
 * Users persist to a JSON file (low volume; Phase 1). Sessions and one-time
 * magic-link tokens live in memory (cleared on restart, which just forces a
 * re-login). Swap for SQLite/Postgres if this ever needs scale.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname } from 'node:path';
import { nanoid } from 'nanoid';

const DATA_FILE = process.env.STUDIO_DATA_FILE || new URL('../data/users.json', import.meta.url).pathname;

function load() {
  try {
    return JSON.parse(readFileSync(DATA_FILE, 'utf8'));
  } catch {
    return { users: {} };
  }
}

let db = load();

function persist() {
  const dir = dirname(DATA_FILE);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const tmp = DATA_FILE + '.tmp';
  writeFileSync(tmp, JSON.stringify(db, null, 2));
  // atomic-ish replace
  writeFileSync(DATA_FILE, JSON.stringify(db, null, 2));
}

// ---- Users (email + password / magic-link) -------------------------------- #
export function findUserByEmail(email) {
  return db.users[email.toLowerCase()] || null;
}

export function createUser({ email, passHash = null }) {
  email = email.toLowerCase();
  if (db.users[email]) throw Object.assign(new Error('account already exists'), { code: 'EXISTS' });
  const user = { id: 'u-' + nanoid(12), email, passHash, plan: 'free', createdAt: Date.now() };
  db.users[email] = user;
  persist();
  return user;
}

export function upsertUserByEmail(email) {
  return findUserByEmail(email) || createUser({ email });
}

export function setUserPlan(email, plan, until = null) {
  const u = findUserByEmail(email);
  if (!u) return null;
  u.plan = plan;
  u.planUntil = until;
  persist();
  return u;
}

// ---- GitHub PAT (encrypted blob) ------------------------------------------ #
// Authenticated users get the blob persisted on their user record. Anonymous
// identities (no email/user row) get an in-memory map keyed by identity, so an
// anon session's connection lasts only as long as the broker process.
const anonGithub = new Map(); // identity -> encBlob

export function setGithubToken(emailOrId, encBlob) {
  const u = emailOrId ? findUserByEmail(emailOrId) : null;
  if (u) { u.githubTokenEnc = encBlob; persist(); return; }
  anonGithub.set(String(emailOrId), encBlob);
}

export function getGithubToken(emailOrId) {
  const u = emailOrId ? findUserByEmail(emailOrId) : null;
  if (u) return u.githubTokenEnc || null;
  return anonGithub.get(String(emailOrId)) || null;
}

export function clearGithubToken(emailOrId) {
  const u = emailOrId ? findUserByEmail(emailOrId) : null;
  if (u) { delete u.githubTokenEnc; persist(); return; }
  anonGithub.delete(String(emailOrId));
}

// ---- ENA inference key (encrypted blob) ----------------------------------- #
// Per-user pool API key for ENA chat (each user supplies their own; usage bills
// to them). Same storage shape as the GitHub PAT.
const anonEna = new Map(); // identity -> encBlob

export function setEnaKey(emailOrId, encBlob) {
  const u = emailOrId ? findUserByEmail(emailOrId) : null;
  if (u) { u.enaKeyEnc = encBlob; persist(); return; }
  anonEna.set(String(emailOrId), encBlob);
}

export function getEnaKey(emailOrId) {
  const u = emailOrId ? findUserByEmail(emailOrId) : null;
  if (u) return u.enaKeyEnc || null;
  return anonEna.get(String(emailOrId)) || null;
}

export function clearEnaKey(emailOrId) {
  const u = emailOrId ? findUserByEmail(emailOrId) : null;
  if (u) { delete u.enaKeyEnc; persist(); return; }
  anonEna.delete(String(emailOrId));
}

// ---- Optional TOTP 2FA ----------------------------------------------------- #
export function setTotpPending(email, secret) {
  const u = findUserByEmail(email); if (!u) return null;
  u.totpPending = secret; persist(); return u;
}
export function enableTotp(email) {
  const u = findUserByEmail(email); if (!u || !u.totpPending) return null;
  u.totpSecret = u.totpPending; u.totpEnabled = true; delete u.totpPending; persist(); return u;
}
export function disableTotp(email) {
  const u = findUserByEmail(email); if (!u) return null;
  u.totpEnabled = false; delete u.totpSecret; delete u.totpPending; persist(); return u;
}

// ---- Sessions (in-memory) -------------------------------------------------- #
const sessions = new Map(); // sid -> { identity, tier, email|null, plan, expires }

export function createSession({ identity, tier, email = null, plan = 'free', ttlMs }) {
  const sid = nanoid(32);
  sessions.set(sid, { identity, tier, email, plan, expires: Date.now() + ttlMs });
  return sid;
}

export function getSession(sid) {
  const s = sessions.get(sid);
  if (!s) return null;
  if (Date.now() > s.expires) { sessions.delete(sid); return null; }
  return s;
}

export function deleteSession(sid) {
  sessions.delete(sid);
}

export function sessionCount() {
  return sessions.size;
}

// ---- One-time tokens (magic links) ---------------------------------------- #
const tokens = new Map(); // token -> { email, expires }

export function createMagicToken(email, ttlMs = 15 * 60 * 1000) {
  const token = nanoid(40);
  tokens.set(token, { email: email.toLowerCase(), expires: Date.now() + ttlMs });
  return token;
}

export function consumeMagicToken(token) {
  const rec = tokens.get(token);
  if (!rec) return null;
  tokens.delete(token);
  if (Date.now() > rec.expires) return null;
  return rec.email;
}
