import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const internalSecret = process.env.AICF_INTERNAL_SECRET ?? 'dev-internal-secret';
const tickMs = Number(process.env.SCHEDULER_TICK_MS ?? 3000);

async function tick() {
  const response = await fetch(`${baseUrl}/internal/scheduler/tick`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-internal-secret': internalSecret
    },
    body: '{}'
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`scheduler tick failed: ${response.status} ${JSON.stringify(payload)}`);
  }

  const assigned = Array.isArray(payload.assigned) ? payload.assigned.length : 0;
  const skipped = Array.isArray(payload.skipped) ? payload.skipped.length : 0;
  if (assigned > 0 || skipped > 0) {
    console.log(`[scheduler] assigned=${assigned} skipped=${skipped}`);
  }
}

async function main() {
  console.log(`[scheduler] started baseUrl=${baseUrl} tickMs=${tickMs}`);
  for (;;) {
    try {
      await tick();
    } catch (error) {
      console.error('[scheduler] tick error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
