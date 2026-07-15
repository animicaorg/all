import { spawn } from 'node:child_process';
import { config } from './config';

// Guarded on-chain send for creator withdrawals + payouts.
// Uses the local-sign CLI (`animica tx send --value-nanm`) with the ABSOLUTE binary path —
// systemd PATH does NOT contain `animica` (the animica.net worker's latent ENOENT bug). Never
// proxies wallet.send through nginx. The worker layer (payout-worker.ts) enforces caps + confirm.

export interface SendResult {
  ok: boolean;
  txid?: string;
  error?: string;
}

export function sendAnmNanm(toAddress: string, amountNanm: bigint, fromLabel = config.treasuryLabel): Promise<SendResult> {
  return new Promise((resolve) => {
    const args = [
      'tx', 'send',
      '--from', fromLabel,
      '--to', toAddress,
      '--value-nanm', amountNanm.toString(),
      '--yes',
    ];
    const env = { ...process.env, ANIMICA_WALLETS_FILE: config.walletsFile };
    const child = spawn(config.cli, args, { env });
    let out = '', err = '';
    child.stdout.on('data', (d) => (out += d.toString()));
    child.stderr.on('data', (d) => (err += d.toString()));
    child.on('error', (e) => resolve({ ok: false, error: `spawn failed: ${e.message}` }));
    child.on('close', (code) => {
      if (code !== 0) return resolve({ ok: false, error: (err || out || `exit ${code}`).trim().slice(0, 500) });
      // Parse a txid (0x...) from CLI output.
      const m = (out + err).match(/0x[0-9a-fA-F]{16,}/);
      resolve({ ok: true, txid: m ? m[0] : undefined });
    });
  });
}
