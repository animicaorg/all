import type { Logger } from '../logger.js';
import type { ReserveService } from '../services/reserveService.js';

export class ReconciliationJob {
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly reserve: ReserveService,
    private readonly logger: Logger,
    private readonly intervalMs = 60_000
  ) {}

  start() {
    if (this.timer) return;
    this.timer = setInterval(async () => {
      try {
        const snapshot = await this.reserve.captureSnapshot('RECONCILIATION');
        this.logger.info({ snapshotId: snapshot.id }, 'reserve reconciliation snapshot captured');
      } catch (error) {
        this.logger.error({ error }, 'reserve reconciliation job failed');
      }
    }, this.intervalMs);
  }

  stop() {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
  }
}
