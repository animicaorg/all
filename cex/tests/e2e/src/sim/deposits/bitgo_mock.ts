/**
 * BitGo Mock Webhook Simulator
 * 
 * Simulates BitGo webhook callbacks for deposit testing without
 * requiring actual blockchain transactions or BitGo credentials.
 */

import * as crypto from 'crypto';

export interface BitGoWebhookConfig {
  /** Webhook URL to call */
  webhookUrl: string;
  /** Shared secret for HMAC signature */
  webhookSecret: string;
  /** Wallet ID */
  walletId: string;
  /** Coin type (btc, eth, etc) */
  coin: string;
}

export interface MockDepositParams {
  /** Recipient address */
  address: string;
  /** Amount in base units (satoshis for BTC) */
  amount: string;
  /** Number of confirmations */
  confirmations: number;
  /** Transaction hash */
  txHash?: string;
  /** Block hash */
  blockHash?: string;
  /** Block height */
  blockHeight?: number;
}

/**
 * Generate BitGo webhook payload
 */
export function generateWebhookPayload(
  config: BitGoWebhookConfig,
  deposit: MockDepositParams
): any {
  const txHash = deposit.txHash || generateTxHash();
  const blockHash = deposit.blockHash || generateBlockHash();
  const blockHeight = deposit.blockHeight || 800000;
  
  // BitGo webhook structure (simplified)
  const payload = {
    type: 'transfer',
    walletId: config.walletId,
    coin: config.coin,
    transfer: {
      id: generateTransferId(),
      coin: config.coin,
      wallet: config.walletId,
      txid: txHash,
      height: blockHeight,
      heightId: `${blockHeight}-${txHash}`,
      date: new Date().toISOString(),
      confirmations: deposit.confirmations,
      value: deposit.amount,
      valueString: deposit.amount,
      feeString: '0',
      payGoFeeString: '0',
      usd: calculateUSD(deposit.amount),
      state: deposit.confirmations >= 2 ? 'confirmed' : 'unconfirmed',
      tags: [],
      history: [],
      entries: [
        {
          address: deposit.address,
          wallet: config.walletId,
          value: deposit.amount,
          valueString: deposit.amount,
        },
      ],
      outputs: [
        {
          id: `${txHash}:0`,
          address: deposit.address,
          value: deposit.amount,
          valueString: deposit.amount,
          wallet: config.walletId,
          chain: 0,
          index: 0,
        },
      ],
      inputs: [],
    },
    hash: blockHash,
  };
  
  return payload;
}

/**
 * Generate HMAC signature for webhook
 */
export function generateHMAC(payload: any, secret: string): string {
  const hmac = crypto.createHmac('sha256', secret);
  hmac.update(JSON.stringify(payload));
  return hmac.digest('hex');
}

/**
 * Send mock webhook
 */
export async function sendMockWebhook(
  config: BitGoWebhookConfig,
  deposit: MockDepositParams
): Promise<{ success: boolean; response?: any; error?: Error }> {
  try {
    const payload = generateWebhookPayload(config, deposit);
    const signature = generateHMAC(payload, config.webhookSecret);
    
    const response = await fetch(config.webhookUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-BitGo-Signature': signature,
      },
      body: JSON.stringify(payload),
    });
    
    const responseData = await response.json();
    
    return {
      success: response.ok,
      response: responseData,
    };
    
  } catch (error) {
    return {
      success: false,
      error: error as Error,
    };
  }
}

/**
 * Simulate deposit with progressive confirmations
 */
export async function simulateDeposit(
  config: BitGoWebhookConfig,
  params: {
    address: string;
    amount: string;
    maxConfirmations?: number;
    confirmationDelay?: number;
  }
): Promise<void> {
  const maxConf = params.maxConfirmations || 6;
  const delay = params.confirmationDelay || 1000;
  
  const txHash = generateTxHash();
  const blockHash = generateBlockHash();
  const blockHeight = 800000 + Math.floor(Math.random() * 1000);
  
  console.log(`[BitGo Mock] Simulating deposit: ${params.amount} to ${params.address}`);
  console.log(`[BitGo Mock] TxHash: ${txHash}`);
  
  for (let conf = 0; conf <= maxConf; conf++) {
    console.log(`[BitGo Mock] Sending webhook with ${conf} confirmations`);
    
    const result = await sendMockWebhook(config, {
      address: params.address,
      amount: params.amount,
      confirmations: conf,
      txHash,
      blockHash,
      blockHeight,
    });
    
    if (!result.success) {
      console.error(`[BitGo Mock] Webhook failed:`, result.error);
      throw result.error;
    }
    
    console.log(`[BitGo Mock] Webhook delivered successfully`);
    
    // Wait before next confirmation
    if (conf < maxConf) {
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
  
  console.log(`[BitGo Mock] Deposit simulation complete`);
}

/**
 * Helper: Generate random transaction hash
 */
function generateTxHash(): string {
  return crypto.randomBytes(32).toString('hex');
}

/**
 * Helper: Generate random block hash
 */
function generateBlockHash(): string {
  return crypto.randomBytes(32).toString('hex');
}

/**
 * Helper: Generate BitGo transfer ID
 */
function generateTransferId(): string {
  return crypto.randomBytes(16).toString('hex');
}

/**
 * Helper: Calculate mock USD value
 */
function calculateUSD(amount: string): number {
  // Simple mock: assume 1 satoshi = 0.0001 USD
  return parseInt(amount) * 0.0001;
}

/**
 * Verify webhook signature
 */
export function verifyWebhookSignature(
  payload: any,
  signature: string,
  secret: string
): boolean {
  const expectedSignature = generateHMAC(payload, secret);
  return signature === expectedSignature;
}

/**
 * Create multiple deposits for testing
 */
export async function simulateMultipleDeposits(
  config: BitGoWebhookConfig,
  deposits: Array<{
    address: string;
    amount: string;
  }>,
  options: {
    maxConfirmations?: number;
    confirmationDelay?: number;
    batchDelay?: number;
  } = {}
): Promise<void> {
  const batchDelay = options.batchDelay || 2000;
  
  for (let i = 0; i < deposits.length; i++) {
    const deposit = deposits[i];
    
    console.log(`[BitGo Mock] Processing deposit ${i + 1}/${deposits.length}`);
    
    await simulateDeposit(config, {
      address: deposit.address,
      amount: deposit.amount,
      maxConfirmations: options.maxConfirmations,
      confirmationDelay: options.confirmationDelay,
    });
    
    // Wait between deposits
    if (i < deposits.length - 1) {
      await new Promise(resolve => setTimeout(resolve, batchDelay));
    }
  }
  
  console.log(`[BitGo Mock] All deposits simulated`);
}
