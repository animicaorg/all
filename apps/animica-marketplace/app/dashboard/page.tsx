import type { Metadata } from 'next';
import DashboardClient from './DashboardClient';

export const metadata: Metadata = {
  title: 'Customer Dashboard — Animica Managed Services',
  description: 'Track your managed Animica deployments, billing status and support tickets.',
  alternates: { canonical: 'https://dashboard.animica.dev/' },
  robots: { index: false },
};

export const dynamic = 'force-dynamic';

export default function DashboardPage() {
  return <DashboardClient />;
}
