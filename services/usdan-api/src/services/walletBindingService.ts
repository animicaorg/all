import jwt from 'jsonwebtoken';
import { ApiError } from '../lib/errors.js';
import type { Config } from '../config.js';
import type { UsdanStore } from '../store/types.js';

export class WalletBindingService {
  constructor(
    private readonly store: UsdanStore,
    private readonly config: Config
  ) {}

  async bindWallet(input: {
    userId: string;
    walletAddress: string;
    chainId: number;
    signature: string;
    message: string;
    isPrimary?: boolean;
  }) {
    if (!input.signature || !input.message) {
      throw new ApiError(400, 'BAD_SIGNATURE', 'Wallet signature and message are required');
    }

    const link = await this.store.upsertWalletLink({
      userId: input.userId,
      walletAddress: input.walletAddress,
      chainId: input.chainId,
      isPrimary: Boolean(input.isPrimary)
    });

    return link;
  }

  issueSessionToken(userId: string, walletAddress: string): string {
    return jwt.sign(
      {
        sub: userId,
        walletAddress,
        scope: 'user'
      },
      this.config.USDAN_API_JWT_SECRET,
      { expiresIn: '4h' }
    );
  }
}
