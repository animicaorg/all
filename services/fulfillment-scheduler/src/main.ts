import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const internalSecret = process.env.AICF_INTERNAL_SECRET ?? 'change-me-internal';
const tickMs = Number(process.env.FULFILLMENT_SCHEDULER_TICK_MS ?? 2500);
const limit = Number(process.env.FULFILLMENT_SCHEDULER_LIMIT ?? 50);

async function tick() {
  const response = await fetch(`${baseUrl}/internal/contract-jobs/scheduler/tick?limit=${limit}`, {
    method: 'POST',
    headers: {
      'x-internal-secret': internalSecret
    }
  });

  if (!response.ok) {
    throw new Error(`scheduler tick failed ${response.status}: ${await response.text()}`);
  }

  const payload = (await response.json()) as {
    assigned?: Array<{ jobId: string }>;
    skipped?: string[];
  };

  const assigned = payload.assigned?.length ?? 0;
  const skipped = payload.skipped?.length ?? 0;
  if (assigned > 0 || skipped > 0) {
    console.log(`[fulfillment-scheduler] assigned=${assigned} skipped=${skipped}`);
  }
}

async function loop() {
  for (;;) {
    try {
      await tick();
    } catch (error) {
      console.error('[fulfillment-scheduler] error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

loop().catch((error) => {
  console.error(error);
  process.exit(1);
});
