import { resolvePlan } from '@/lib/cloud/entitlements';
import { cloudSession } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import AnalyticsClient from './AnalyticsClient';

export const dynamic = 'force-dynamic';

// /cloud/analytics — server shell (auth gate) + client that reads the developer's
// day-bucketed series from GET /api/cloud/v1/me/analytics.
export default async function CloudAnalyticsPage() {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const plan = await resolvePlan(sess.accountId);
  return <AnalyticsClient premium={plan.limits.premium_analytics} planKey={plan.key} />;
}
