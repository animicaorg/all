import type { Metadata } from 'next';
import BillingClient from './BillingClient';

export const metadata: Metadata = {
  title: 'Billing & Plan — Animica',
  description: 'Manage your Animica subscription: plan, usage meters, over-limit choices and billing history.',
  robots: { index: false },
};

export const dynamic = 'force-dynamic';

export default function BillingPage() {
  return <BillingClient />;
}
