import { NextRequest } from 'next/server';
import { publicOk, publicPreflight, err } from '@/lib/api';
import { fleetStats } from '@/lib/cloud/dispatch';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// PUBLIC compute-network stats (§23). Every number is a live DB aggregate — invented
// provider statistics are explicitly forbidden, so when the network is empty this
// endpoint says so with zeros.
export async function GET(_req: NextRequest) {
  try {
    const s = await fleetStats();
    return publicOk({
      providers_online: s.providersOnline,
      providers_registered: s.providersRegistered,
      capacity: {
        cpu_cores_online: s.cpuCoresOnline,
        memory_mb_online: s.memoryMbOnline,
        gpus_online: s.gpusOnline,
      },
      jobs: {
        pending: s.jobsPending,
        in_flight: s.jobsInFlight,
        completed: s.jobsCompleted,
        failed: s.jobsFailed,
      },
      paid_to_providers_nanm: s.paidToProvidersNanm,
      provider_share_bps: s.providerShareBps,
      lease_seconds: s.leaseSeconds,
      required_runtime: s.requiredRuntime,
      register: '/api/cloud/v1/providers/register',
    });
  } catch (e) {
    return err(e);
  }
}

export async function OPTIONS() {
  return publicPreflight();
}
