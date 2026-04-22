import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const internalSecret = process.env.AICF_INTERNAL_SECRET ?? 'change-me-internal';
const tickMs = Number(process.env.CONTRACT_JOB_WATCHER_TICK_MS ?? 3000);
const maxEvents = Number(process.env.CONTRACT_JOB_WATCHER_LIMIT ?? 100);

async function poll() {
  const response = await fetch(`${baseUrl}/internal/chain-events/watch?limit=${maxEvents}`, {
    headers: {
      'x-internal-secret': internalSecret
    }
  });

  if (!response.ok) {
    throw new Error(`watch failed ${response.status}: ${await response.text()}`);
  }

  const payload = (await response.json()) as { events?: Array<{ id: string; eventType: string }> };
  const events = payload.events ?? [];

  if (events.length > 0) {
    console.log(`[contract-job-watcher] consumed=${events.length} last=${events[events.length - 1]?.eventType ?? 'n/a'}`);
  }
}

async function loop() {
  for (;;) {
    try {
      await poll();
    } catch (error) {
      console.error('[contract-job-watcher] error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

loop().catch((error) => {
  console.error(error);
  process.exit(1);
});
