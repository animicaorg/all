/**
 * BitGo Mock Withdrawal Simulator
 * 
 * Simulates BitGo withdrawal flow for testing without
 * actual blockchain transactions.
 */

import * as crypto from 'crypto';

export interface MockWithdrawalParams {
  /** Destination address */
  address: string;
  /** Amount in base units */
  amount: string;
  /** User ID initiating withdrawal */
  userId: string;
  /** Asset/coin type */
  coin: string;
}

export interface MockWithdrawalResult {
  withdrawalId: string;
  txHash: string;
  status: 'pending' | 'signed' | 'broadcast' | 'confirmed';
  confirmations: number;
  fee: string;
}

/**
 * Simulate BitGo withdrawal flow
 */
export async function simulateBitGoWithdrawal(
  params: MockWithdrawalParams,
  options: {
    /** Webhook URL for status updates */
    webhookUrl?: string;
    /** Simulate signing delay (ms) */
    signingDelay?: number;
    /** Simulate broadcast delay (ms) */
    broadcastDelay?: number;
    /** Simulate confirmation delay (ms) */
    confirmationDelay?: number;
    /** Number of confirmations to simulate */
    maxConfirmations?: number;
  } = {}
): Promise<MockWithdrawalResult> {
  const withdrawalId = generateWithdrawalId();
  const txHash = generateTxHash();
  
  console.log(`[BitGo Mock Withdrawal] Starting withdrawal ${withdrawalId}`);
  console.log(`[BitGo Mock Withdrawal] ${params.amount} ${params.coin} to ${params.address}`);
  
  // Step 1: Pending (created)
  let result: MockWithdrawalResult = {
    withdrawalId,
    txHash: '',
    status: 'pending',
    confirmations: 0,
    fee: calculateFee(params.amount, params.coin),
  };
  
  if (options.webhookUrl) {
    await sendWithdrawalWebhook(options.webhookUrl, result, params);
  }
  
  // Step 2: Signing
  await delay(options.signingDelay || 1000);
  result.status = 'signed';
  console.log(`[BitGo Mock Withdrawal] Transaction signed`);
  
  if (options.webhookUrl) {
    await sendWithdrawalWebhook(options.webhookUrl, result, params);
  }
  
  // Step 3: Broadcast
  await delay(options.broadcastDelay || 2000);
  result.status = 'broadcast';
  result.txHash = txHash;
  console.log(`[BitGo Mock Withdrawal] Transaction broadcast: ${txHash}`);
  
  if (options.webhookUrl) {
    await sendWithdrawalWebhook(options.webhookUrl, result, params);
  }
  
  // Step 4: Confirmations
  const maxConf = options.maxConfirmations || 6;
  const confDelay = options.confirmationDelay || 3000;
  
  for (let conf = 1; conf <= maxConf; conf++) {
    await delay(confDelay);
    result.confirmations = conf;
    
    if (conf >= 2) {
      result.status = 'confirmed';
    }
    
    console.log(`[BitGo Mock Withdrawal] ${conf} confirmations`);
    
    if (options.webhookUrl) {
      await sendWithdrawalWebhook(options.webhookUrl, result, params);
    }
  }
  
  console.log(`[BitGo Mock Withdrawal] Withdrawal complete`);
  
  return result;
}

/**
 * Send withdrawal status webhook
 */
async function sendWithdrawalWebhook(
  webhookUrl: string,
  result: MockWithdrawalResult,
  params: MockWithdrawalParams
): Promise<void> {
  const payload = {
    type: 'withdrawal_update',
    withdrawalId: result.withdrawalId,
    userId: params.userId,
    coin: params.coin,
    address: params.address,
    amount: params.amount,
    fee: result.fee,
    txHash: result.txHash || null,
    status: result.status,
    confirmations: result.confirmations,
    timestamp: new Date().toISOString(),
  };
  
  try {
    await fetch(webhookUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    console.error(`[BitGo Mock Withdrawal] Webhook failed:`, error);
  }
}

/**
 * Simulate batch withdrawals
 */
export async function simulateBatchWithdrawals(
  withdrawals: MockWithdrawalParams[],
  options: {
    webhookUrl?: string;
    batchDelay?: number;
    signingDelay?: number;
    broadcastDelay?: number;
    confirmationDelay?: number;
    maxConfirmations?: number;
  } = {}
): Promise<MockWithdrawalResult[]> {
  const results: MockWithdrawalResult[] = [];
  const batchDelay = options.batchDelay || 5000;
  
  for (let i = 0; i < withdrawals.length; i++) {
    const withdrawal = withdrawals[i];
    
    console.log(`[BitGo Mock Withdrawal] Processing withdrawal ${i + 1}/${withdrawals.length}`);
    
    const result = await simulateBitGoWithdrawal(withdrawal, options);
    results.push(result);
    
    // Delay between withdrawals
    if (i < withdrawals.length - 1) {
      await delay(batchDelay);
    }
  }
  
  return results;
}

/**
 * Simulate failed withdrawal
 */
export async function simulateFailedWithdrawal(
  params: MockWithdrawalParams,
  failureReason: 'insufficient_funds' | 'invalid_address' | 'network_error',
  webhookUrl?: string
): Promise<{
  withdrawalId: string;
  error: string;
}> {
  const withdrawalId = generateWithdrawalId();
  
  console.log(`[BitGo Mock Withdrawal] Simulating failed withdrawal: ${failureReason}`);
  
  const errorMessages = {
    insufficient_funds: 'Insufficient funds in hot wallet',
    invalid_address: 'Invalid destination address',
    network_error: 'Network error during broadcast',
  };
  
  const result = {
    withdrawalId,
    error: errorMessages[failureReason],
  };
  
  if (webhookUrl) {
    const payload = {
      type: 'withdrawal_failed',
      withdrawalId,
      userId: params.userId,
      coin: params.coin,
      address: params.address,
      amount: params.amount,
      error: result.error,
      timestamp: new Date().toISOString(),
    };
    
    try {
      await fetch(webhookUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      console.error(`[BitGo Mock Withdrawal] Webhook failed:`, error);
    }
  }
  
  return result;
}

/**
 * Helper: Generate withdrawal ID
 */
function generateWithdrawalId(): string {
  return `wd_${crypto.randomBytes(16).toString('hex')}`;
}

/**
 * Helper: Generate transaction hash
 */
function generateTxHash(): string {
  return crypto.randomBytes(32).toString('hex');
}

/**
 * Helper: Calculate withdrawal fee
 */
function calculateFee(amount: string, coin: string): string {
  // Simple mock fee calculation
  const feeRates: Record<string, number> = {
    btc: 0.0001,
    eth: 0.001,
    usdt: 1,
  };
  
  const rate = feeRates[coin.toLowerCase()] || 0.0001;
  return String(Math.floor(parseFloat(amount) * rate));
}

/**
 * Helper: Delay
 */
function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}
