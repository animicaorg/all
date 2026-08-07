import type { Metadata } from 'next';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';
import { serviceDto } from '@/lib/hire';
import { PAYPAL_CLIENT_ID, paypalEnv } from '@/lib/paypal';
import HireClient from './HireClient';

export const metadata: Metadata = {
  title: 'Hire Me — Managed Animica Infrastructure',
  description:
    'Order professionally managed Animica deployments with recurring monthly billing: dedicated mining pools, RPC endpoints / full nodes, managed websites and custom infrastructure — deployed, monitored and maintained for you.',
  alternates: { canonical: 'https://animica.dev/hire' },
  openGraph: {
    title: 'Hire Me — Managed Animica Infrastructure',
    description: 'Professionally managed deployments with ongoing support and monthly billing.',
    url: 'https://animica.dev/hire',
    siteName: 'Animica',
    type: 'website',
  },
  twitter: { card: 'summary_large_image', title: 'Hire Me — Managed Animica Infrastructure' },
};

export const dynamic = 'force-dynamic';

export default async function HirePage() {
  const services = await prisma.hireService.findMany({
    where: { active: true },
    orderBy: [{ sortOrder: 'asc' }, { createdAt: 'asc' }],
  });
  return (
    <HireClient
      services={jsonSafe(services.map(serviceDto))}
      paypal={{ clientId: PAYPAL_CLIENT_ID, env: paypalEnv }}
    />
  );
}
