import { aicfApi } from './api';
import type { SessionState } from './session';
import { connectWallet, getChainId, signMessage } from './wallet';

export type WalletAuthRole = 'developer' | 'provider';

export type WalletAuthResult = {
  session: SessionState;
  address: string;
  chainId: number;
  createdAccount: boolean;
};

export function normalizeWalletAddress(value: string): string {
  return value.trim().toLowerCase();
}

function walletIdentityEmail(address: string, chainId: number): string {
  const cleaned = normalizeWalletAddress(address).replace(/[^a-z0-9]/g, '');
  const suffix = cleaned.slice(-20) || 'wallet';
  return `wallet-${chainId}-${suffix}@wallet.aicf.animica`;
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

async function walletPassword(address: string, chainId: number): Promise<string> {
  const digest = await sha256Hex(`aicf-wallet-auth-v2::${normalizeWalletAddress(address)}::${chainId}`);
  return `Aicf!${digest.slice(0, 24)}`;
}

async function walletCredentials(address: string, chainId: number) {
  return {
    email: walletIdentityEmail(address, chainId),
    password: await walletPassword(address, chainId)
  };
}

function shouldSignup(error: unknown): boolean {
  return (error as Error).message.toLowerCase().includes('invalid credentials');
}

export async function signInWithWallet(role: WalletAuthRole): Promise<WalletAuthResult> {
  const accounts = await connectWallet();
  const first = accounts[0];
  if (!first) {
    throw new Error('Wallet returned no connected accounts');
  }

  const address = normalizeWalletAddress(first);
  const chainId = (await getChainId()) ?? 1337;
  const signature = await signMessage(
    `AICF wallet sign in\naddress: ${address}\nchain: ${chainId}\nintent: wallet-login`
  );
  if (!signature) {
    throw new Error('Wallet signature was rejected');
  }

  const credentials = await walletCredentials(address, chainId);

  let authPayload: { token: string; user: SessionState['user'] };
  let createdAccount = false;

  try {
    authPayload = await aicfApi.login({
      email: credentials.email,
      password: credentials.password
    });
  } catch (error) {
    if (!shouldSignup(error)) {
      throw error;
    }
    authPayload = await aicfApi.signup({
      email: credentials.email,
      password: credentials.password,
      role
    });
    createdAccount = true;
  }

  const linkedUser = await aicfApi.linkWallet(
    {
      token: authPayload.token,
      user: authPayload.user
    },
    {
      address,
      chainId,
      signature
    }
  );

  return {
    session: {
      token: authPayload.token,
      user: linkedUser.user
    },
    address,
    chainId,
    createdAccount
  };
}
