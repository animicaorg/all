'use client';

// Browser wallet connect + login for the marketplace UI.
//
// v1 flow (unscoped, existing web-UI connect — Studio / My AI / .anm Names):
//   animica_requestAccounts -> GET challenge -> animica_signMessage -> animica_getPublicKey ->
//   POST /auth/verify (server ml_dsa_65-verifies) -> httpOnly full-rights session cookie.
//
// v2 flow (purpose-bound, used by the Developer Center /dev and the storefront):
//   GET challenge?purpose=<devportal|store|subscribe|review> (a single-purpose string the wallet
//   signs) -> POST /auth/verify with the purpose -> httpOnly session scoped to that purpose only.
//   The purpose is bound INSIDE the signed string and burned single-use server-side, so a signature
//   obtained for one purpose can never mint a session for another (the shipped extension still
//   blind-signs — see lib/challenge.ts). Signed via the same window.animica provider (browser
//   extension or the mobile wallet's dapp-browser).
//
// Extension only (wallet.animica.org is discontinued). ml_dsa_65 is the only accepted scheme.

import type { ApiScope } from '@/lib/config';

declare global {
  interface Window { animica?: any }
}

export function hasWallet(): boolean {
  return typeof window !== 'undefined' && !!window.animica?.isAnimica;
}

function provider(): any {
  const w = typeof window !== 'undefined' ? window.animica : undefined;
  if (!w?.isAnimica) throw new Error('Animica wallet extension not found. Install it from animica.org/wallet.');
  return w;
}

// Prompt for (and normalise) the active account address.
async function requestAddress(w: any): Promise<string> {
  const accounts: string[] = await w.request({ method: 'animica_requestAccounts' });
  const address = (accounts?.[0] ?? '').toLowerCase();
  if (!address) throw new Error('no account');
  return address;
}

// Sign `challenge` and resolve the address's ml_dsa_65 public key — the two artefacts /auth/verify
// needs to ml_dsa_65-verify the login. Identical signing call for v1 and v2 (the wallet prepends the
// `animica:signMessage:` domain; the server verifies over domain + challenge).
async function signChallenge(w: any, address: string, challenge: string): Promise<{ signature: string; publicKey: string }> {
  const signature: string = await w.request({ method: 'animica_signMessage', params: [{ message: challenge }] });
  const pk = await w.request({ method: 'animica_getPublicKey', params: [{ address }] });
  const publicKey: string = typeof pk === 'string' ? pk : pk?.publicKey;
  return { signature, publicKey };
}

// v1: unscoped full-rights session. Backward-compatible — Studio / My AI / Names call this.
export async function connectAndLogin(): Promise<{ address: string }> {
  const w = provider();
  const address = await requestAddress(w);

  const chRes = await fetch(`/api/mkt/v1/auth/challenge?address=${encodeURIComponent(address)}`);
  const chData = await chRes.json();
  if (!chRes.ok) throw new Error(chData?.error?.message ?? 'challenge failed');
  const challenge: string = chData.challenge;

  const { signature, publicKey } = await signChallenge(w, address, challenge);

  const vRes = await fetch('/api/mkt/v1/auth/verify', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ address, challenge, signature, publicKey }),
  });
  const vData = await vRes.json();
  if (!vRes.ok) throw new Error(vData?.error?.message ?? 'login failed');
  return { address };
}

export type ChallengePurpose = 'devportal' | 'store' | 'subscribe' | 'review';
export interface PurposeLogin { address: string; purpose: ChallengePurpose; scopes: ApiScope[] }

// v2: purpose-bound login. `extra` entries are bound into the signed string as consent params
// (e.g. subscribe: { listing, period, amount }); leave empty for devportal/store/review.
// Returns the granted scopes so the caller can reflect what the session can actually do.
export async function loginWithPurpose(
  purpose: ChallengePurpose,
  extra: Record<string, string> = {},
): Promise<PurposeLogin> {
  const w = provider();
  const address = await requestAddress(w);

  const qs = new URLSearchParams({ address, purpose });
  for (const [k, v] of Object.entries(extra)) qs.set(k, v);
  const chRes = await fetch(`/api/mkt/v1/auth/challenge?${qs.toString()}`);
  const chData = await chRes.json();
  if (!chRes.ok) throw new Error(chData?.error?.message ?? 'challenge failed');
  const challenge: string = chData.challenge;

  const { signature, publicKey } = await signChallenge(w, address, challenge);

  const vRes = await fetch('/api/mkt/v1/auth/verify', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ address, challenge, signature, publicKey, purpose }),
  });
  const vData = await vRes.json();
  if (!vRes.ok) throw new Error(vData?.error?.message ?? 'login failed');
  return { address, purpose, scopes: (vData.scopes ?? []) as ApiScope[] };
}

// Developer Center login — mints a session scoped to ['publish','withdraw','read']. Used by /dev.
export function connectDevPortal(): Promise<PurposeLogin> {
  return loginWithPurpose('devportal');
}

export async function pay(to: string, valueNanm: string | bigint): Promise<string> {
  const w = window.animica;
  if (!w?.isAnimica) throw new Error('wallet not found');
  const from = (await w.request({ method: 'animica_accounts' }))?.[0];
  const txid: string = await w.request({
    method: 'animica_sendTransaction',
    params: [{ from, to, value: valueNanm.toString() }],
  });
  return txid;
}
