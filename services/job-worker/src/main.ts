import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const internalSecret = process.env.AICF_INTERNAL_SECRET ?? 'dev-internal-secret';
const tickMs = Number(process.env.JOB_WORKER_TICK_MS ?? 4000);
const fallbackLimit = Number(process.env.JOB_WORKER_FALLBACK_LIMIT ?? 5);

async function runFallback() {
  const response = await fetch(`${baseUrl}/internal/jobs/first-party-fallback?limit=${fallbackLimit}`, {
    method: 'POST',
    headers: {
      'x-internal-secret': internalSecret
    }
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`fallback execution failed: ${response.status} ${JSON.stringify(payload)}`);
  }

  const completed = Array.isArray(payload.completed) ? payload.completed.length : 0;
  if (completed > 0) {
    console.log(`[job-worker] completed first-party fallback jobs=${completed}`);
  }
}

async function main() {
  console.log(`[job-worker] started baseUrl=${baseUrl} tickMs=${tickMs}`);
  for (;;) {
    try {
      await runFallback();
    } catch (error) {
      console.error('[job-worker] iteration error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
