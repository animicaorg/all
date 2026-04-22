import type { Config } from '../config.js';
import { sha256Hex } from '../lib/crypto.js';
import type { TreasuryProvider } from '../providers/treasury/provider.js';
import type { ReserveSnapshotRecord, UsdanStore } from '../store/types.js';

export interface ChainReadClient {
  getUsdanTotalSupply(): Promise<string>;
}

export class StubChainReadClient implements ChainReadClient {
  constructor(private supply = '0.00') {}

  setSupply(next: string): void {
    this.supply = next;
  }

  async getUsdanTotalSupply(): Promise<string> {
    return this.supply;
  }
}

export class ReserveService {
  constructor(
    private readonly store: UsdanStore,
    private readonly treasury: TreasuryProvider,
    private readonly chain: ChainReadClient,
    private readonly config: Config
  ) {}

  async captureSnapshot(source: ReserveSnapshotRecord['source'] = 'RECONCILIATION'): Promise<ReserveSnapshotRecord> {
    const [ledger, supply] = await Promise.all([
      this.treasury.getLedgerSummary(),
      this.chain.getUsdanTotalSupply()
    ]);

    const pendingMints = await this.store.listPurchaseIntents();
    const pendingMintsAmount = pendingMints
      .filter((x) => ['FUNDS_SETTLED', 'MINT_AUTHORIZED', 'MINT_SUBMITTED'].includes(x.status))
      .reduce((sum, rec) => sum + Number(rec.amountUsdan), 0);

    const redemptions = await this.store.listRedemptionRequests();
    const outstandingRedemptions = redemptions
      .filter((x) => ['ONCHAIN_PENDING', 'ONCHAIN_CONFIRMED', 'PAYOUT_PENDING', 'PAYOUT_SENT'].includes(x.status))
      .reduce((sum, rec) => sum + Number(rec.amountUsdan), 0);

    const reserveBalance = Number(ledger.settledBalanceUsd);
    const liabilities = Number(supply);
    const coverageRatioBps = liabilities > 0 ? Math.floor((reserveBalance / liabilities) * 10_000) : 0;

    const reconciliationHash = sha256Hex(
      JSON.stringify({
        asOfIso: ledger.asOfIso,
        reserveBalance,
        liabilities,
        pendingMintsAmount,
        outstandingRedemptions
      })
    );

    return this.store.createReserveSnapshot({
      source,
      tokenSupply: supply,
      reserveLedgerBalance: reserveBalance.toFixed(2),
      outstandingRedemptionQueue: outstandingRedemptions.toFixed(2),
      pendingMintQueue: pendingMintsAmount.toFixed(2),
      coverageRatioBps,
      latestAttestationAt: undefined,
      attestationHash: undefined,
      attestationUri: undefined,
      reconciliationHash,
      signedStatementHash: undefined
    });
  }

  async getDashboard() {
    const [snapshots, purchases, redemptions] = await Promise.all([
      this.store.listReserveSnapshots(1),
      this.store.listPurchaseIntents(),
      this.store.listRedemptionRequests()
    ]);

    const latest = snapshots[0] ?? (await this.captureSnapshot());
    const pendingMintQueue = purchases
      .filter((x) => ['FUNDS_SETTLED', 'MINT_AUTHORIZED', 'MINT_SUBMITTED'].includes(x.status))
      .reduce((sum, x) => sum + Number(x.amountUsdan), 0);

    const outstandingRedemptionQueue = redemptions
      .filter((x) => ['ONCHAIN_PENDING', 'ONCHAIN_CONFIRMED', 'PAYOUT_PENDING', 'PAYOUT_SENT'].includes(x.status))
      .reduce((sum, x) => sum + Number(x.amountUsdan), 0);

    return {
      tokenSupply: latest.tokenSupply,
      reserveLedgerBalance: latest.reserveLedgerBalance,
      outstandingRedemptionQueue: outstandingRedemptionQueue.toFixed(2),
      pendingMintQueue: pendingMintQueue.toFixed(2),
      latestAttestationTimestamp: latest.latestAttestationAt,
      coverageRatioBps: latest.coverageRatioBps,
      minCoverageBps: this.config.USDAN_RESERVE_MIN_COVERAGE_BPS,
      reconciliationHash: latest.reconciliationHash
    };
  }
}
