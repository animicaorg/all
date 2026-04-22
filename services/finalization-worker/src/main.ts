import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const internalSecret = process.env.AICF_INTERNAL_SECRET ?? 'change-me-internal';
const tickMs = Number(process.env.FINALIZATION_WORKER_TICK_MS ?? 3000);
const limit = Number(process.env.FINALIZATION_WORKER_LIMIT ?? 20);

async function tick() {
  const response = await fetch(`${baseUrl}/internal/contract-jobs/finalization/tick?limit=${limit}`, {
    method: 'POST',
    headers: {
      'x-internal-secret': internalSecret
    }
  });

  if (!response.ok) {
    throw new Error(`finalization tick failed ${response.status}: ${await response.text()}`);
  }

  const payload = (await response.json()) as { finalized?: string[]; skipped?: string[] };
  const finalized = payload.finalized?.length ?? 0;
  const skipped = payload.skipped?.length ?? 0;
  if (finalized > 0 || skipped > 0) {
    console.log(`[finalization-worker] finalized=${finalized} skipped=${skipped}`);
  }
}

async function loop() {
  for (;;) {
    try {
      await tick();
    } catch (error) {
      console.error('[finalization-worker] error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

loop().catch((error) => {
  console.error(error);
  process.exit(1);
});
