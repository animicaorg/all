import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const internalSecret = process.env.AICF_INTERNAL_SECRET ?? 'change-me-internal';
const tickMs = Number(process.env.RESULT_SUBMITTER_TICK_MS ?? 2500);
const limit = Number(process.env.RESULT_SUBMITTER_LIMIT ?? 50);

async function tick() {
  const response = await fetch(`${baseUrl}/internal/contract-jobs/result-submitter/tick?limit=${limit}`, {
    method: 'POST',
    headers: {
      'x-internal-secret': internalSecret
    }
  });

  if (!response.ok) {
    throw new Error(`result submitter tick failed ${response.status}: ${await response.text()}`);
  }

  const payload = (await response.json()) as { moved?: string[] };
  const moved = payload.moved?.length ?? 0;
  if (moved > 0) {
    console.log(`[result-submitter] moved=${moved}`);
  }
}

async function loop() {
  for (;;) {
    try {
      await tick();
    } catch (error) {
      console.error('[result-submitter] error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

loop().catch((error) => {
  console.error(error);
  process.exit(1);
});
