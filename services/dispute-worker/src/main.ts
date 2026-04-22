import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const adminToken = process.env.AICF_ADMIN_SESSION_TOKEN ?? '';
const autoResolve = process.env.DISPUTE_WORKER_AUTO_RESOLVE === '1';
const tickMs = Number(process.env.DISPUTE_WORKER_TICK_MS ?? 9000);

async function pollDisputes() {
  if (!adminToken) {
    console.log('[dispute-worker] missing AICF_ADMIN_SESSION_TOKEN; skipping dispute review');
    return;
  }

  const resp = await fetch(`${baseUrl}/admin/disputes`, {
    headers: {
      'x-session-token': adminToken
    }
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`dispute list failed: ${resp.status} ${text}`);
  }

  const body = (await resp.json()) as { disputes?: Array<{ id: string }> };
  const disputes = body.disputes ?? [];
  console.log(`[dispute-worker] open_disputes=${disputes.length}`);

  if (!autoResolve) return;

  for (const dispute of disputes) {
    const resolve = await fetch(`${baseUrl}/admin/disputes/${dispute.id}/resolve`, {
      method: 'POST',
      headers: {
        'x-session-token': adminToken,
        'content-type': 'application/json'
      },
      body: JSON.stringify({
        action: 'uphold_provider',
        note: 'auto-resolved by dispute worker'
      })
    });

    if (resolve.ok) {
      console.log(`[dispute-worker] resolved_dispute=${dispute.id}`);
    }
  }
}

async function main() {
  console.log(`[dispute-worker] started baseUrl=${baseUrl} tickMs=${tickMs} autoResolve=${autoResolve}`);
  for (;;) {
    try {
      await pollDisputes();
    } catch (error) {
      console.error('[dispute-worker] polling error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
