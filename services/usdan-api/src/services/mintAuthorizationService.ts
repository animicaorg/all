import { randomUUID } from 'node:crypto';
import { hmacSha256Hex, sha256Hex } from '../lib/crypto.js';
import type { Config } from '../config.js';
import type { MintAuthorizationRecord, UsdanStore } from '../store/types.js';

export class MintAuthorizationService {
  constructor(
    private readonly store: UsdanStore,
    private readonly config: Config
  ) {}

  buildDigest(input: {
    userId: string;
    walletAddress: string;
    amountUsdan: string;
    requestId: string;
    nonce: string;
  }): string {
    const canonical = [
      this.config.ANIMICA_CHAIN_ID,
      this.config.ANIMICA_USDAN_MINT_CONTROLLER_ADDRESS,
      input.userId,
      input.walletAddress,
      input.amountUsdan,
      input.requestId,
      input.nonce
    ].join('|');
    return sha256Hex(canonical);
  }

  async prepare(purchaseIntentId: string, userId: string, walletAddress: string, amountUsdan: string): Promise<MintAuthorizationRecord> {
    const requestId = `mint_${randomUUID()}`;
    const nonce = randomUUID();
    const digestHex = this.buildDigest({ userId, walletAddress, amountUsdan, requestId, nonce });

    return this.store.createMintAuthorization({
      purchaseIntentId,
      userId,
      walletAddress,
      amountUsdan,
      requestId,
      nonce,
      digestHex,
      signatureHex: undefined,
      status: 'PREPARED',
      txHash: undefined
    });
  }

  async sign(authorization: MintAuthorizationRecord): Promise<MintAuthorizationRecord> {
    const signatureHex = hmacSha256Hex(this.config.ANIMICA_MINT_SIGNER_PRIVATE_KEY, authorization.digestHex);
    return this.store.updateMintAuthorization(authorization.id, {
      signatureHex,
      status: 'SIGNED'
    });
  }

  async markSubmitted(authorizationId: string, txHash: string): Promise<MintAuthorizationRecord> {
    return this.store.updateMintAuthorization(authorizationId, {
      status: 'SUBMITTED',
      txHash
    });
  }

  async markConfirmed(authorizationId: string, txHash: string): Promise<MintAuthorizationRecord> {
    return this.store.updateMintAuthorization(authorizationId, {
      status: 'CONFIRMED',
      txHash
    });
  }
}
